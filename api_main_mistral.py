import json
from typing import Any, Dict, List, Optional, Iterator, Sequence

from fastapi import FastAPI

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
from mcp_utils import MAIA_TOOLS
from maia_prompt import BASE_PROMPT
from maia_page_context import CURRENT_PAGE_CONTEXT

class MAIAChatModel(BaseChatModel):
    client_model: Any = None
    model_type: str = None
    model_name: str = None
    
    mistral_key: Optional[str] = None
    
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 1024
    
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
        mistral_resp = self.client_model.chat.complete(**mistral_params)
        client_msg = mistral_resp.choices[0].message
        
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

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None

class ChatResponse(BaseModel):
    answer: str
    session_id: str

MAX_HISTORY_MESSAGES = 20

SESSIONS: dict[str, list] = {}

app = FastAPI()
maia_llm = MAIAChatModel(
    tools=[convert_to_openai_tool(t) for t in MAIA_TOOLS], 
    mistral_key='***',
)

@app.post("/chat")
async def chat(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    
    session_history = SESSIONS.get(session_id, [])
    
    # Add the prompt in front of the messages
    session_history.insert(0, SystemMessage(content=BASE_PROMPT + f"\n\nPAGE CONTEXT:\n{json.dumps(CURRENT_PAGE_CONTEXT, indent=2)}"))
    
    session_history.append(HumanMessage(content=request.message))
    
    llm_msg = maia_llm.invoke(session_history)
    
    tool_call_secure_cpt = 0
    while llm_msg.tool_calls and tool_call_secure_cpt < 10:
        session_history.append(llm_msg)
        for tool_call in llm_msg.tool_calls:
            if tool_call["name"] == "get_damage_type_ids":
                tool_output = get_damage_type_ids.invoke(tool_call["args"])
            elif tool_call["name"] == "get_country_ids_by_name":
                tool_output = get_country_ids_by_name.invoke(tool_call["args"])
            elif tool_call["name"] == "get_turbine_model_ids_by_name":
                tool_output = get_turbine_model_ids_by_name.invoke(tool_call["args"])
            elif tool_call["name"] == "get_sites":
                tool_output = get_sites.invoke(tool_call["args"])
            
            elif tool_call["name"] == "count_damage_on_turbines":
                tool_output = count_damage_on_turbines.invoke(tool_call["args"])
            elif tool_call["name"] == "count_damage_on_a_site_turbines":
                tool_output = count_damage_on_a_site_turbines.invoke(tool_call["args"])
            elif tool_call["name"] == "estimate_repair_cost":
                tool_output = estimate_repair_cost.invoke(tool_call["args"])
            elif tool_call["name"] == "get_individual_damage_infos":
                tool_output = get_individual_damage_infos.invoke(tool_call["args"])
            elif tool_call["name"] ==  "estimate_damage_repair_frequency":
                tool_output = estimate_damage_repair_frequency.invoke(tool_call["args"])
            
            elif tool_call["name"] == "get_wind_index_explanation":
                tool_output = get_wind_index_explanation.invoke(tool_call["args"])
            elif tool_call["name"] == "what_is_new":
                tool_output = what_is_new.invoke(tool_call["args"])
            
            session_history.append(
                ToolMessage(
                    content=tool_output,
                    tool_call_id=tool_call["id"],
                    name=tool_call["name"],
                )
            )
            
        llm_msg = maia_llm.invoke(session_history)
        tool_call_secure_cpt += 1
    
    print("ANSWER", llm_msg.content)
    session_history.append(AIMessage(content=llm_msg.content))
    
    # Remove the prompt before saving
    session_history = session_history[1:]
    
    if len(session_history) > MAX_HISTORY_MESSAGES:
        session_history = session_history[-MAX_HISTORY_MESSAGES:]
        
    SESSIONS[session_id] = session_history
    
    return {"answer": llm_msg.content, "session_id": session_id}