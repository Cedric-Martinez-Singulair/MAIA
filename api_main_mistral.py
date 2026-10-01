import json
import os
import time
import re
from typing import Any, Dict, List, Optional, Iterator, Sequence
from typing import Literal
import threading
from contextlib import asynccontextmanager
import httpx
from dataclasses import asdict, dataclass, field

from fastapi import FastAPI, HTTPException

from lingua import Language, LanguageDetectorBuilder


from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import create_react_agent

from langchain_core.tools import tool

import uuid
from pydantic import BaseModel, ValidationError
from mistralai.client import Mistral
from anthropic import Anthropic

from get_ids_mcp import *
from general_mcp import *
from damage_mcp import *
from mcp_utils import MAIA_TOOLS
from maia_utils import TW_DB_CURSOR, GLOBAL_INFOS
from maia_prompt import *
from maia_page_context import get_report_page_context


GLOBAL_STATE: dict = {}

DETECTED_LANGUAGES = [
    Language.FRENCH, Language.ENGLISH, Language.SPANISH,
    Language.GERMAN, Language.ITALIAN, Language.PORTUGUESE,
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # with_preloaded_language_models : sinon la première détection paie le
    # chargement des modèles n-grammes, en pleine transcription.
    GLOBAL_STATE["detector"] = (
        LanguageDetectorBuilder.from_languages(*DETECTED_LANGUAGES)
        .with_preloaded_language_models()
        .build()
    )
    yield
    GLOBAL_STATE.clear()

MAX_TOOL_CALLS_PER_QUESTION = 20
MAX_TOOL_CALL_SECONDS = 10

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
        
        allowed_tools = kwargs.get("tools", self.tools)
        allowed_tool_choice = kwargs.get("tool_choice", self.tool_choice)
        mistral_params = {
            "model": self.model_name,
            "messages": formatted_messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "max_tokens": self.max_tokens,
            "tools": allowed_tools,
            "tool_choice": allowed_tool_choice,
        }
        
        timeout_ms = MAX_TOOL_CALL_SECONDS*1000
        
        session_id = kwargs.get("session_id", None)
        if session_id is not None:
            if SESSIONS[session_id].recall_status == "second_try":
                timeout_ms *= 2
            elif SESSIONS[session_id].recall_status == "third_try":
                timeout_ms *= 4
        
        try:
            mistral_resp = self.client_model.chat.complete(**mistral_params, timeout_ms=timeout_ms)
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
MIN_CHARS_FOR_DETECTION = 5
MIN_LANGUAGE_CONFIDENCE = 0.15

def detect_language(text: str) -> tuple[str | None, str | None, float | None]:
    """(code ISO, nom, confiance) ou (None, None, None) si peu fiable."""
    if len(text) < MIN_CHARS_FOR_DETECTION:
        return None, None, None

    confidences = GLOBAL_STATE["detector"].compute_language_confidence_values(text)
    if not confidences:
        return None, None, None

    best = confidences[0]
    if best.value < MIN_LANGUAGE_CONFIDENCE:
        return None, None, None

    return (best.language.iso_code_639_1.name.lower(),
            best.language.name.capitalize(),
            best.value)

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
  },
  "status": "new_start"
}

chat_request_example_report = {
  "message": "hello",
  "session_id": "string",
  "company_id": 3,
  "persona_id": "blade_analyst",
  "context": {
    "page_type": "report",
    "planification_id": 14630
  },
  "status": "new_start"
}
# Moisson de Beauce 14630
# Bandirma 16311
# Lokeren S-07915 16683
# Piiparinmäki 22199

# Butera 24413
# Magana 13425
# Malagon I 13454

def trim_history(history: list, max_messages: int) -> list:
    if len(history) <= max_messages:
        return history
    for i in range(len(history) - max_messages, len(history)):
        if isinstance(history[i], HumanMessage):
            return history[i:]
    return history

def abort_pending_turn(history: list) -> list:
    if not history:
        return history
    
    last_human_idx = None
    for msg_i, msg in enumerate(history):
        if isinstance(msg, HumanMessage):
            last_human_idx = msg_i
    
    if last_human_idx is None:
        return history
    return history[:last_human_idx+1]

def build_page_context(context: dict) -> dict:
    if context.get("page_type") == "report":
        return get_report_page_context(context["planification_id"])
    return {"webpage_type": "homepage"}

def build_persona_prompt(persona_id: str, societe_id: int, language: str) -> str:
    persona_prompt = PROMPT_START
    
    if persona_id == "blade_analyst":
        persona_prompt += PROMPT_BLADE_ANALYST
    elif persona_id == "service_manager":
        persona_prompt += PROMPT_SERVICE_MANAGER
    elif persona_id == "capacity_planner":
        persona_prompt += PROMPT_CAPACITY_PLANNER
        
    persona_prompt += PROMPT_END_1
    
    if language == 'fr':
        persona_prompt += FR_SPECIFIC_PROMPT
    
    if societe_id == 15:
        persona_prompt += PROMPT_ENERCON_CHART_EXAMPLE
    else:
        persona_prompt += PROMPT_VESTAS_CHART_EXAMPLE
    persona_prompt += PROMPT_END_2
    
    return persona_prompt

CONTINUE_USER_MESSAGE = {
    "fr": "\n\nContinue l'analyse là où tu t'es arrêté.\n\n",
    "en": "\n\nContinue the analysis from where you stopped.\n\n",
    "de": "\n\nSetzen Sie die Analyse von der Stelle aus fort, an der Sie angehalten haben.\n\n",
}

PARTIAL_SUFFIX = {
    "fr": "\n\nCeci est une réponse partielle, je continue de chercher.\n\n",
    "en": "\n\nThis is a partial answer, I keep looking.\n\n",
    "de": "\n\nDas ist eine Teilantwort, ich suche weiter.\n\n",
}

STOP_ACK = {
    "fr": "\n\nAnalyse interrompue. Vous pouvez poser une nouvelle question.\n\n",
    "en": "\n\nAnalysis stopped. You can ask a new question.\n\n",
    "de": "\n\nAnalyse gestoppt. Sie können eine neue Frage stellen.\n\n",
}

SUMMARIZE_MESSAGE = {
    "fr": "Résume pour l'utilisateur ce que tu as trouvé jusqu'à présent en langage simple. \nN'utilise pas de tool. Reste concis. \nTu vas continuer automatiquement. Ne demande PAS à l'utilisateur ce qu'il souhaiterait",
    "en": "Summarize for the user what you have found so far in plain language. \nDo not call any tools. Keep it concise.\n You will continue automatically. Do NOT ask the user what he would like",
    "de": "Fassen Sie für den Benutzer in einfacher Sprache zusammen, was Sie bisher gefunden haben. \nRufen Sie keine Tools auf. Halten Sie es prägnant.\n Sie fahren automatisch fort. Fragen Sie den Benutzer NICHT, was er möchte",
}

ALREADY_SUMMARIZED_PREFIX = {
    "fr": "Ne répète pas le texte suivant, il a déjà été résumé à l'utilisateur :\n",
    "en": "Do not repeat the following text; it has already been summarized to the user :\n",
    "de": "Wiederholen Sie den folgenden Text nicht; er wurde dem Benutzer bereits zusammengefasst:\n",
}

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    company_id: int
    persona_id: Literal["blade_analyst", "service_manager", "capacity_planner"]
    context: dict
    status: Literal["new_start", "to_continue", "stop"]

class ChatResponse(BaseModel):
    answer: str
    session_id: str
    language: list
    status: Literal["complete", "partial"]

MAX_HISTORY_MESSAGES = 30

@dataclass
class AgentRunResult:
    llm_msg: AIMessage
    working_history: list
    current_api_call_history: list
    tool_calls_used: int
    timed_out: bool
    tool_limit_reached: bool
    paused: bool

@dataclass
class Session:
    id: str
    history: list = field(default_factory=list)
    agent_status: Literal["idle", "paused"] = "idle"
    recall_status: Literal["first_try", "second_try", "third_try"] | None = None
    pending_llm_msg: AIMessage | None = None
    pending_tool_calls: list[dict] | None = None
    already_summarized_text: list = field(default_factory=list)
    
    def get_history_with_prompt(self, full_prompt: str) -> list:
        return [SystemMessage(content=full_prompt)] + list(self.history) # Insert at the beginning of the list
    
    def save_from_working_history(self, working_history: list) -> None:
        self.history = trim_history(working_history[1:], MAX_HISTORY_MESSAGES)
        #TODO history save in DB
    
    def reset_loop_state(self) -> None:
        self.pending_llm_msg = None
        self.pending_tool_calls = None
        
SESSIONS: dict[str, Session] = {}

def get_or_create_session(session_id: str) -> Session:
    global SESSIONS
    
    if session_id not in SESSIONS:
        SESSIONS[session_id] = Session(id=session_id)
    return SESSIONS[session_id]

def run_agent_loop(session_id: str, llm: MAIAChatModel, working_history: list, current_api_call_history: list) -> AgentRunResult:
    global SESSIONS
    
    tool_dict = {t.name: t for t in MAIA_TOOLS}
    tool_calls_used = 0
    timed_out = False
    tool_limit_reached = False
    
    timeout_s = MAX_TOOL_CALL_SECONDS
    if SESSIONS[session_id].recall_status == "second_try":
        timeout_s *= 2
    elif SESSIONS[session_id].recall_status == "third_try":
        timeout_s *= 4
        
    deadline = time.perf_counter() + timeout_s + 5
    
    try:
        if SESSIONS[session_id].pending_llm_msg is None:
            print("DEBUG invoke 1")
            llm_msg = llm.invoke(working_history, session_id=session_id)
            working_history.append(llm_msg)
            current_api_call_history.append(llm_msg)
            SESSIONS[session_id].pending_tool_calls = None
            SESSIONS[session_id].pending_llm_msg = llm_msg
        
        while SESSIONS[session_id].pending_llm_msg.tool_calls:
            if time.perf_counter() > deadline:
                print("DEBUG break 1")
                timed_out = True
                break
            tool_calls_pending = len(SESSIONS[session_id].pending_llm_msg.tool_calls)
            if tool_calls_used + tool_calls_pending > MAX_TOOL_CALLS_PER_QUESTION:
                tool_limit_reached = True
                break

            if SESSIONS[session_id].pending_tool_calls is None:
                SESSIONS[session_id].pending_tool_calls = []
                for tool_call in SESSIONS[session_id].pending_llm_msg.tool_calls:
                    SESSIONS[session_id].pending_tool_calls.append(tool_call)

            while len(SESSIONS[session_id].pending_tool_calls) > 0:
                if time.perf_counter() > deadline:
                    print("DEBUG break 2")
                    timed_out = True
                    break
                
                tool_call = SESSIONS[session_id].pending_tool_calls.pop(0)
                
                tool = tool_dict.get(tool_call["name"])
                print("DEBUG invoke 2")
                try:
                    tool_output = (
                        tool.invoke(tool_call["args"])
                        if tool else f"Unknown tool: {tool_call['name']}"
                    )
                except ValidationError as e:
                    print("VALIDATION ERROR", e)
                    tool_output = "You can't use this tool with thoses arguments." #TODO améliorer
                
                tool_msg = ToolMessage(
                    content=tool_output,
                    tool_call_id=tool_call["id"],
                    name=tool_call["name"],
                )
                working_history.append(tool_msg)
                current_api_call_history.append(tool_msg)
                tool_calls_used += 1
            
            if timed_out or tool_limit_reached:
                break
            if time.perf_counter() > deadline:
                print("DEBUG break 3")
                timed_out = True
                break
            
            print("DEBUG invoke 3")
            llm_msg = llm.invoke(working_history, session_id=session_id)
            working_history.append(llm_msg)
            current_api_call_history.append(llm_msg)
            SESSIONS[session_id].pending_tool_calls = None
            SESSIONS[session_id].pending_llm_msg = llm_msg
    
    except TimeoutError:
        print("DEBUG break 4")
        timed_out = True
    
    paused = timed_out or tool_limit_reached
    
    llm_msg = SESSIONS[session_id].pending_llm_msg
    if not paused:
        SESSIONS[session_id].reset_loop_state()
    
    return AgentRunResult(
        llm_msg=llm_msg,
        working_history=working_history,
        current_api_call_history=current_api_call_history,
        tool_calls_used=tool_calls_used,
        timed_out=timed_out,
        tool_limit_reached=tool_limit_reached,
        paused=paused,
    )
    
SKIPPED_TOOL_MESSAGE = "[Not executed: time or tool limit reached]"

def _tool_responses_after_assistant(history: list, start_index: int) -> dict[str, ToolMessage]:
    found: dict[str, ToolMessage] = {}
    msg_idx = start_index + 1
    while msg_idx < len(history) and isinstance(history[msg_idx], ToolMessage):
        found[history[msg_idx].tool_call_id] = history[msg_idx]
        msg_idx += 1
    return found

def repair_mistral_tool_history(history: list) -> list:
    repaired_history: list = []
    msg_idx = 0
    while msg_idx < len(history):
        msg = history[msg_idx]
        repaired_history.append(msg)
        if isinstance(msg, AIMessage) and msg.tool_calls:
            existing = _tool_responses_after_assistant(history, msg_idx)
            # Keep tool messages that are already in history (in order of tool_calls)
            for tc in msg.tool_calls:
                tc_id = tc.get("id")
                if tc_id in existing:
                    repaired_history.append(existing[tc_id])
                else:
                    repaired_history.append(
                        ToolMessage(
                            content=SKIPPED_TOOL_MESSAGE,
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown_tool"),
                        )
                    )
            # Skip the original ToolMessage block we just replayed/ filled
            msg_idx += 1
            while msg_idx < len(history) and isinstance(history[msg_idx], ToolMessage):
                msg_idx += 1
            continue
        msg_idx += 1
    return repaired_history

def prepare_current_api_call_history(
current_api_call_history: list, 
req_msg: str, session: Session):
    current_api_call_history.append(HumanMessage(content=req_msg))
    
    last_ai_msg_idx = None
    for msg_i, msg in reversed(list(enumerate(session.history))):
        if isinstance(msg, AIMessage):
            last_ai_msg_idx = msg_i; break
    for msg in session.history[last_ai_msg_idx:]:
        current_api_call_history.append(msg)

def summarize_partial_answer(
llm: MAIAChatModel,
current_api_call_history: list,
language: str | None,
session: Session
) -> str:
    safe_history = repair_mistral_tool_history(current_api_call_history)
    safe_history = list(safe_history)
    safe_history.append(HumanMessage(content=(SUMMARIZE_MESSAGE.get(language, SUMMARIZE_MESSAGE["en"]))))
    try:
        summary_msg = llm.invoke(safe_history , tools=None, tool_choice="none")
        summary_msg_text = (summary_msg.content or "").strip()
        if summary_msg_text:
            session.already_summarized_text.append(summary_msg_text)
            return summary_msg_text + PARTIAL_SUFFIX.get(language, PARTIAL_SUFFIX["en"])
    except (TimeoutError, Exception) as e:
        print("Partial summary failed:", e)
    
    fallback = {
        "fr": "\n\nJe réfléchis...\n\n",
        "en": "\n\nI'm thinking...\n\n",
        "de": "\n\nIch denke...\n\n",
    }
    return fallback.get(language, fallback["en"])

@app.post("/chat")
async def chat(request: ChatRequest):
    global SESSIONS
    
    print("DEBUG 1")
    GLOBAL_INFOS['CURRENT_COMPANY_ID'] = request.company_id
    TW_DB_CURSOR.execute("SELECT label_en FROM societes WHERE id = %s", (request.company_id, ))
    user_company = 'Singulair' # Default value
    for row in TW_DB_CURSOR:
        user_company = row[0]
    GLOBAL_INFOS['CURRENT_COMPANY'] = user_company
    
    if GLOBAL_INFOS['CURRENT_PERSONA'] is None: #TODO update
        GLOBAL_INFOS['CURRENT_PERSONA'] = request.persona_id
    
    session_id = request.session_id or str(uuid.uuid4())
    session = get_or_create_session(session_id)
    
    ### Récupération de la langue
    language, language_name, confidence = detect_language(request.message)

    persona_prompt = build_persona_prompt(GLOBAL_INFOS['CURRENT_PERSONA'], request.company_id, language)
    current_page_context = build_page_context(request.context)
    full_prompt = persona_prompt + "\n\nPAGE CONTEXT:\n" + str(current_page_context)
    
    # History used to summarize the current progress in case of time out
    current_api_call_history = [SystemMessage(content=full_prompt)]
    prepare_current_api_call_history(current_api_call_history, request.message, session)
    
    ### MAIN LOOP ###
    
    if request.status == "stop":
        print("DEBUG stop")
        
        SESSIONS[session_id].reset_loop_state()
        
        if session.agent_status == "paused":
            session.history = abort_pending_turn(session.history)
        
        session.agent_status = "idle"
        ack_msg = STOP_ACK.get(language, STOP_ACK["en"])
        session.history.append(AIMessage(content=ack_msg))
        session.history = trim_history(session.history, MAX_HISTORY_MESSAGES)
        #TODO history save in DB
        
        print("ACK MSG", ack_msg)
        return {
            "answer": ack_msg,
            "session_id": session_id,
            "language": [language, language_name, confidence],
            "status": "complete"
        }
        
    if request.status == "to_continue":
        print("DEBUG to continue")
        
        if SESSIONS[session_id].recall_status == "first_try":
            SESSIONS[session_id].recall_status = "second_try"
        
        elif SESSIONS[session_id].recall_status == "second_try":
            SESSIONS[session_id].recall_status = "third_try"
            
        print(session.pending_llm_msg)
        print(session.pending_tool_calls)
        
        if session.agent_status != "paused":
            print("DEBUG to_continue while no paused")
            raise HTTPException(
                status_code=400,
                detail="No paused analysis to continue for this session.",
            )
        
        working_history = [SystemMessage(content=full_prompt)]
        
        already_summerize_full_text = ALREADY_SUMMARIZED_PREFIX.get(language, ALREADY_SUMMARIZED_PREFIX["en"])
        for text in session.already_summarized_text:
            already_summerize_full_text += text + "\n"
        already_summerize_full_text += CONTINUE_USER_MESSAGE.get(language, CONTINUE_USER_MESSAGE["en"])
        
        working_history.append(HumanMessage(content=already_summerize_full_text))
        working_history += list(session.history)
        
        print("DEBUG WORKING HISTORY", working_history)
        
        llm_timer_start = time.perf_counter()
        result = run_agent_loop(session_id, maia_llm, working_history, current_api_call_history)
        print(f"ELAPSED TIME {time.perf_counter() - llm_timer_start:.2f}s")
        
        if result.paused:
            print("DEBUG continue paused")
            session.agent_status = "paused"
            session.save_from_working_history(result.working_history)
            llm_answer = summarize_partial_answer(
                maia_llm, result.current_api_call_history, language, session
            )
            
            print("ANSWER", llm_answer)
            return {
                "answer": llm_answer,
                "session_id": session_id,
                "language": [language, language_name, confidence],
                "status": "partial"
            }
        
        session.agent_status = "idle"
        session.save_from_working_history(result.working_history)
        llm_answer = result.llm_msg.content or ""
        
        print("DEBUG continue 2")
        
        print("ANSWER", llm_answer)
        return {
            "answer": llm_answer,
            "session_id": session_id,
            "language": [language, language_name, confidence],
            "status": "complete"
        }
    
    print("DEBUG New start ")
    
    SESSIONS[session_id].recall_status = "first_try"
    SESSIONS[session_id].reset_loop_state()
    
    #TODO verify this, it's not supposed to happened
    if session.agent_status == "paused":
        print("DEBUG agent paused")
        session.history = abort_pending_turn(session.history)
        session.agent_status = "idle"
    
    working_history = session.get_history_with_prompt(full_prompt)
    working_history.append(HumanMessage(content=request.message))
    
    llm_timer_start = time.perf_counter()
    result = run_agent_loop(session_id, maia_llm, working_history, current_api_call_history)
    print(f"ELAPSED TIME {time.perf_counter() - llm_timer_start:.2f}s")
    
    if result.paused:
        print("DEBUG paused")
        session.agent_status = "paused"
        session.save_from_working_history(result.working_history)
        llm_answer = summarize_partial_answer(
            maia_llm, result.current_api_call_history, language, session
        )
        
        print("ANSWER", llm_answer)
        return {
            "answer": llm_answer,
            "session_id": session_id,
            "language": [language, language_name, confidence],
            "status": "partial"
        }
    
    print("DEBUG 3")
    
    session.agent_status = "idle"
    session.save_from_working_history(result.working_history)
    llm_answer = result.llm_msg.content or ""
    
    print("ANSWER", llm_answer)
    return {
        "answer": llm_answer,
        "session_id": session_id,
        "language": [language, language_name, confidence],
        "status": "complete"
    }

# How should i priorize my repair campaign in spain
# On the found sites with an AEP > 500 MWh which one has the most erosion evolution ?
# What is the mean of the erosion evolution on each of these sites ?

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/health/db")
async def healthDB():
    try:
        TW_DB_CURSOR.execute("SELECT 1")
        return {"status": "ok"}
    except:
        os._exit(1)
    