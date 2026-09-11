import json
import time
from typing import Any, Dict, List, Optional, Iterator, Sequence
from typing import Literal
import threading
from contextlib import asynccontextmanager
import httpx

from fastapi import FastAPI

from lingua import Language, LanguageDetectorBuilder


from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import create_react_agent

from langchain_core.tools import tool

import uuid
from pydantic import BaseModel
from mistralai.client import Mistral
from anthropic import Anthropic

from get_ids_mcp import *
from general_mcp import *
from damage_mcp import *
from turbine_mcp import *
from mcp_utils import MAIA_TOOLS
from maia_utils import TW_DB_CURSOR, GLOBAL_INFOS
from maia_prompt import *
from maia_page_context import get_report_page_context


STATE: dict = {}


DETECTED_LANGUAGES = [
    Language.FRENCH, Language.ENGLISH, Language.SPANISH,
    Language.GERMAN, Language.ITALIAN, Language.PORTUGUESE,
]

@asynccontextmanager
async def lifespan(app: FastAPI):

    # with_preloaded_language_models : sinon la première détection paie le
    # chargement des modèles n-grammes, en pleine transcription.
    STATE["detector"] = (
        LanguageDetectorBuilder.from_languages(*DETECTED_LANGUAGES)
        .with_preloaded_language_models()
        .build()
    )
    yield
    STATE.clear()


MAX_TOOL_CALLS_PER_QUESTION = 20
MAX_ANSWER_SECONDS = 30

class MAIAChatModel(BaseChatModel):
    client_model: Any = None
    model_type: str = None
    model_name: str = None
    
    mistral_key: Optional[str] = None
    
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 4096
    
    tools: List[Any] = None
    tool_choice: str = "auto"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.client_model is None:
            if self.mistral_key is None:
                raise ValueError("An api_key must be provided")
            else:
                self.model_type = 'mistral-chat'
                self.client_model = Mistral(api_key=self.mistral_key)
                self.model_name = 'mistral-medium-2508'
    
    @property # What is that ?
    def _llm_type(self) -> str:
        return self.model_type
    
    def _generate(self,
    messages: List[BaseMessage],
    stop: Optional[List[str]] = None,
    run_manager: Optional[CallbackManagerForLLMRun] = None,
    **kwargs: Any,) -> ChatResult:
        formatted_messages = []
        tool_call_cpt = 0
        
        for msg in messages:
            if isinstance(msg, SystemMessage):
                formatted_messages.append({"role": "system", "content": msg.content})
                
            elif isinstance(msg, HumanMessage):
                formatted_messages.append({"role": "user", "content": msg.content})
                
            elif isinstance(msg, AIMessage):
                message_dict = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    tool_calls = []
                    for tool_call in msg.tool_calls:
                        tool_calls.append(
                            {
                                "id": tool_call.get("id", f"call_{len(tool_calls)}"),
                                "type": "function",
                                "function": {
                                    "name": tool_call["name"],
                                    "arguments": json.dumps(tool_call["args"])
                                    if isinstance(tool_call["args"], dict)
                                    else tool_call["args"],
                                },
                            }
                        )
                    message_dict["tool_calls"] = tool_calls

                formatted_messages.append(message_dict)
                
            elif isinstance(msg, ToolMessage):
                formatted_messages.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "name": msg.name,
                        "tool_call_id": msg.tool_call_id,
                    }
                )
        
        mistral_params = {
            "model": self.model_name,
            "messages": formatted_messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "max_tokens": self.max_tokens,
            "tools": self.tools,
            "tool_choice": self.tool_choice,
        }
        
        try:
            mistral_resp = self.client_model.chat.complete(**mistral_params, timeout_ms=MAX_ANSWER_SECONDS*1000)
            client_msg = mistral_resp.choices[0].message
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            raise TimeoutError("Mistral call timed out") from e
        
        if mistral_resp.choices[0].finish_reason == 'length':
            print("DEBUG TRUNCATED")
        
        tool_calls = []
        if hasattr(client_msg, "tool_calls") and client_msg.tool_calls:
            for tool_call in client_msg.tool_calls:
                args = tool_call.function.arguments
                
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                tool_calls.append(
                    {
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "args": args,
                    }
                )
        if tool_calls:
            message = AIMessage(
                content=client_msg.content or "",
                tool_calls=tool_calls,
                additional_kwargs={"tool_calls": tool_calls},
            )
        else:
            message = AIMessage(content=client_msg.content or "")
        
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

### API PART ###

# En dessous, la détection n'est pas fiable : on préfère ne rien annoncer.
MIN_CHARS_FOR_DETECTION = 10
MIN_LANGUAGE_CONFIDENCE = 0.40

def detect_language(text: str) -> tuple[str | None, str | None, float | None]:
    """(code ISO, nom, confiance) ou (None, None, None) si peu fiable."""
    if len(text) < MIN_CHARS_FOR_DETECTION:
        return None, None, None

    confidences = STATE["detector"].compute_language_confidence_values(text)
    if not confidences:
        return None, None, None

    best = confidences[0]
    if best.value < MIN_LANGUAGE_CONFIDENCE:
        return None, None, None

    return (best.language.iso_code_639_1.name.lower(),
            best.language.name.capitalize(),
            best.value)

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    company_id: int
    persona_id: Literal["blade_analyst", "service_manager", "capacity_planner"]
    context: dict

class ChatResponse(BaseModel):
    answer: str
    session_id: str

MAX_HISTORY_MESSAGES = 20

SESSIONS: dict[str, list] = {}

app = FastAPI(lifespan=lifespan)
maia_llm = MAIAChatModel(
    tools=[convert_to_openai_tool(t) for t in MAIA_TOOLS], 
    mistral_key='***',
)

# TO COPY IN THE SWAGGER
chat_request_example_home = {
  "message": "hello",
  "session_id": "string",
  "company_id": 3,
  "persona_id": "service_manager",
  "context": {
    "page_type": "homepage"
  }
}

chat_request_example_report = {
  "message": "hello",
  "session_id": "string",
  "company_id": 3,
  "persona_id": "service_manager",
  "context": {
    "page_type": "report",
    "planification_id": 14630
  }
}
# Moisson de Beauce planification_id = 14630
# Butera planification_id = 24413
# Magana planification_id = 13425
# Malagon I planification_id = 13454
# Piiparinmäki planification_id = 22199

def trim_history(history, max_messages):
    if len(history) <= max_messages:
        return history
    for i in range(len(history) - max_messages, len(history)):
        if isinstance(history[i], HumanMessage):
            return history[i:]
    return []

@app.post("/chat")
async def chat(request: ChatRequest):
    GLOBAL_INFOS['CURRENT_COMPANY_ID'] = request.company_id
    TW_DB_CURSOR.execute("SELECT label_en FROM societes WHERE id = %s", (request.company_id, ))
    user_company = 'Singulair' # Default value
    for row in TW_DB_CURSOR:
        user_company = row[0]
    GLOBAL_INFOS['CURRENT_COMPANY'] = user_company
    
    GLOBAL_INFOS['CURRENT_PERSONA'] = request.persona_id
    
    full_prompt = PROMPT_START
    if GLOBAL_INFOS['CURRENT_PERSONA'] == 'blade_analyst':
        full_prompt += PROMPT_BLADE_ANALYST
    elif GLOBAL_INFOS['CURRENT_PERSONA'] == 'service_manager':
        full_prompt += PROMPT_SERVICE_MANAGER
    elif GLOBAL_INFOS['CURRENT_PERSONA'] == 'capacity_planner':
        full_prompt += PROMPT_CAPACITY_PLANNER
    full_prompt += PROMPT_END
    
    session_id = request.session_id or str(uuid.uuid4())
    
    session_history = SESSIONS.get(session_id, [])
    
    # Add the prompt in front of the messages
    if request.context['page_type'] == 'report':
        current_page_context = get_report_page_context(request.context['planification_id'])
    else:
        current_page_context = {'webpage_type': 'homepage'}
    
    print("PROMPT", full_prompt)
    session_history.insert(0, SystemMessage(content=full_prompt + "\n\nPAGE CONTEXT:\n" + str(current_page_context)))
    
    print("USER REQUEST", request.message)
    session_history.append(HumanMessage(content=request.message))
    
    ###R2CUP2RATION DE LA LANGUE
    language, language_name, confidence = detect_language(request.message)
    llm_timer_start = time.perf_counter()
    
    tool_call_limit_reached = False
    try:
        print([type(m).__name__ for m in session_history])
        llm_msg = maia_llm.invoke(session_history)
        
        tool_call_secure_cpt = 0; 
        
        timed_out_deadline = time.perf_counter() + MAX_ANSWER_SECONDS
        timed_out = False
        
        tool_dict = {t.name: t for t in MAIA_TOOLS}
        while llm_msg.tool_calls:
            if time.perf_counter() > timed_out_deadline:
                timed_out = True; break
            
            tool_call_secure_cpt += len(llm_msg.tool_calls)
            print("TOOL CALL CPT", tool_call_secure_cpt)
            if tool_call_secure_cpt > MAX_TOOL_CALLS_PER_QUESTION:
                tool_call_limit_reached = True; break
                
            session_history.append(llm_msg)
            
            for tool_call in llm_msg.tool_calls:
                tool = tool_dict.get(tool_call["name"])
                tool_output = tool.invoke(tool_call["args"]) if tool else f"Unknown tool: {tool_call['name']}"
                
                session_history.append(
                    ToolMessage(
                        content=tool_output,
                        tool_call_id=tool_call["id"],
                        name=tool_call["name"],
                    )
                )
                
            if time.perf_counter() > timed_out_deadline:
                timed_out = True; break
                
            llm_msg = maia_llm.invoke(session_history)
        
    except TimeoutError:
        timed_out = True
    
    llm_timer_elapsed = time.perf_counter() - llm_timer_start
    print(f"ELAPSED TIME {llm_timer_elapsed:.2f}s")
    
    if tool_call_limit_reached:
        if language == 'fr':
            llm_answer = "Cette question est trop vague. Pourriez-vous être plus précis ? Par exemple, de quel site ou de quelle turbine parlez-vous ?"
        elif language == 'en':
            llm_answer = "This question is too vague. Could you be more specific? For example, which site or which turbine are you asking about?"
    elif timed_out:
        if language == 'fr':
            llm_answer = "Il a fallu trop de temps pour répondre à cette question, désolé. Pourriez-vous être plus précis ? Par exemple, quel site ou quelle turbine demandez-vous ?"
        elif language == 'en':
            llm_answer = "It took too long to answer this question, sorry. Could you be more specific? For example, which site or which turbine are you asking about?"
    else:
        llm_answer = llm_msg.content
    
    print("ANSWER", llm_answer)
    
    session_history.append(AIMessage(content=llm_answer))
    
    # Remove the prompt before saving
    session_history = session_history[1:]
    
    session_history = trim_history(session_history, MAX_HISTORY_MESSAGES)
        
    SESSIONS[session_id] = session_history
    
    ####DEBUG####
    if session_id != 'string':
        with open(f"{session_id}.txt", "w") as text_file:
            text_file.write(str(session_history))
    ############
    
   
    
    return {"answer": llm_answer, "session_id": session_id , "language": [language, language_name, confidence]}


# How should i priorize my repair campaign in spain
# On the found sites with an AEP > 500 MWh which one has the most erosion evolution ?
# What is the mean of the erosion evolution on each of these sites ?