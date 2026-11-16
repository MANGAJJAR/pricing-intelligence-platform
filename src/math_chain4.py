#math_chain4.py

import math
from typing import Annotated, Sequence

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langgraph.prebuilt.tool_node import ToolNode
import numexpr
from typing_extensions import TypedDict
from langchain_anthropic import ChatAnthropic
import asyncio
import os
import dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
import httpx
import re, json
import json_repair
from functools import partial

dotenv.load_dotenv("config/.env")

client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
token_url = os.getenv("token_url")
iliad_url = os.getenv("endpoint")

import logging
logger = logging.getLogger("DosageChainLogger")

limits = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=10
)

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=1, min=1, max=10))
async def get_chat_access_token():
    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(
            url=token_url,
            data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret
                  }
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(f"Auth request failed with status {e.response.status_code}: {e.response.text}")
            raise
        data = response.json()
        token = data["access_token"]
        return f"Bearer {token}"

@tool
def calculator(expression: str) -> str:
    """Calculate expression using Python's numexpr library.

    Expression should be a single line mathematical expression
    that solves the problem.

    Examples:
        "37593 * 67" for "37593 times 67"
        "37593**(1/5)" for "37593^(1/5)"
    """
    local_dict = {"pi": math.pi, "e": math.e, "floor": math.floor, "int": int, "round": round,"min": min,"max": max,"abs": abs,}
    try:
        global_context = {"__builtins__": __builtins__, "math": math}
        return str(eval(expression, global_context))
    except Exception as e:
        return f"Error: {e}"

class ChainState(TypedDict):
        messages: Annotated[Sequence[BaseMessage], add_messages]
        iteration_count: int

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=1, min=1, max=10))
async def acall_chain(state: ChainState, config: RunnableConfig, llm_with_tools):
    last_message = state["messages"][-1]
    response = await llm_with_tools.ainvoke(state["messages"], config)
    return {"messages": [response], "iteration_count": state.get("iteration_count", 0)}

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=1, min=1, max=10))
async def acall_model(state: ChainState, config: RunnableConfig, llm_with_tools):
    count = state.get("iteration_count", 0) + 1
    state["iteration_count"] = count
    response = await llm_with_tools.ainvoke(state["messages"], config)
    return {"messages": [response], "iteration_count": count}

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=1, min=1, max=10))
async def m_chain(query, llm_anthropic, ndc=None, indication=None, extra_vars=None):

    llm = llm_anthropic

    tools = [calculator]
    llm_with_tools = llm.bind_tools(tools)

    def needs_tool_use(state: ChainState) -> str:
        count = state.get("iteration_count", 0)
        last_msg = state["messages"][-1]
        # print(last_msg)
        needs_tool = hasattr(last_msg, "tool_calls") and last_msg.tool_calls  #Checks if obj.attr exists
        if needs_tool and count < 15:
            return "execute_tool"
        else:
            return END

    # Build the graph
    graph_builder = StateGraph(ChainState)
    graph_builder.add_node("call_tool", partial(acall_chain, llm_with_tools=llm_with_tools))
    graph_builder.add_node("execute_tool", ToolNode(tools))
    graph_builder.add_node("call_model", partial(acall_model, llm_with_tools=llm_with_tools))

    graph_builder.set_entry_point("call_tool")
    graph_builder.add_conditional_edges("call_tool", needs_tool_use)
    # graph_builder.add_edge("call_tool", "execute_tool")
    graph_builder.add_edge("execute_tool", "call_model")

    graph_builder.add_conditional_edges("call_model", needs_tool_use)
    chain = graph_builder.compile()

    # Start with user query
    state = {
        "messages": [("user", query)],
        "iteration_count": 0
    }

    messages = []
    async for event in chain.astream(state, stream_mode="values"):
        msg = event["messages"][-1]
        messages.append(msg)
        event["messages"][-1].pretty_print()
        #log
        log_data = {
                "ndc": ndc,
                "indication": indication,
                "message_role": getattr(msg, "type", None),
                "message_content": getattr(msg, "content", None)
            }
        if extra_vars:
            log_data.update(extra_vars)

        try:
            logger.info(log_data)
        except Exception as e:
            logger.warning(f"Failed to log message: {e}")

    if messages:
        # final_text = messages[-1].content.strip()
        content = messages[-1].content
        if isinstance(content, str):
            final_text = content.strip()
        else:
            final_text = str(content)
        if isinstance(content, list):
            print(content)
            return content
        print("Final text------------------------")
        print(final_text)
        yearly_dosage_match = re.search(r'(\[.*\])', final_text, re.DOTALL)
        if yearly_dosage_match:
            yearly_dosage_fixed = json_repair.repair_json(yearly_dosage_match.group(1))
            yearly_dosage_data = json.loads(yearly_dosage_fixed)
            print(yearly_dosage_data)
            return yearly_dosage_data
    else:
        return None

if __name__ == "__main__":
    # AUTH_TOKEN = get_chat_access_token()
    AUTH_TOKEN =asyncio.run(get_chat_access_token())

    llm = ChatAnthropic(
        base_url=iliad_url + "/anthropic",
        model="claude-3-7-sonnet-20250219",
        api_key=f"{AUTH_TOKEN}",
        default_headers={"Authorization": f"{AUTH_TOKEN}", "anthropic-beta": "tools-2024-04-04"},
        model_kwargs={"extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"}},
        temperature=0.0,
        max_tokens=4096
    )

    query="""
    You are an expert medical data processor and calculator.
    Your task is to calculate the 'Yearly Dosage', 'Yearly Cycles' in respective unit based on the based on the 'Recommended Dosage': 300 mg (three consecutive subcutaneous injections of 100 mg each) at the onset of the cluster period, and then monthly until the end of the cluster period,
    'Cycle Duration': q4w (monthly) and
    'Duration of Treatment': Until the end of the cluster period.

    **Important Interpretation Rules for Dosing Schedules:**
    1.  **"X mg weekly for Y weeks"**: This means Y individual administrations, one per week.
    2.  **"Split infusion on Day A and Day B" or similar "split dose" phrasing**: This describes how a *single dose* for that specified period (e.g., a single weekly dose) is administered, not that multiple full doses are given. For example, "1050 mg weekly (split on Day 1 and Day 2)" means *one* 1050 mg dose is given per week, delivered in two parts. It does not mean two 1050 mg doses are given in that week.
    3.  **"Starting Week Z onwards"**: The new dosing schedule begins *at* Week Z. If there's a gap between the end of a previous phase and Week Z, no doses are given during that gap.
    4.  **Implicit Start**: If a subsequent dosing phase is described without an explicit "starting Week Z", assume it begins immediately after the preceding phase ends.
    5.  **Duration of Treatment as Limiting Factor (First Year):**
            - Finite duration (e.g., "4 weeks", "6 months", "N/A" for as-needed): First year dosage/doses **must be capped at this specified duration**.
            - Continuous duration (e.g., "Continuous", "Extended periods"): Calculate for the full 52 weeks in the first year.
            - For "Multiple cycles" with a specified minimum interval (e.g., "at least X days/weeks between cycles"), always calculate the *maximum* number of complete cycles that can occur within the 52-week (365-day) first year, using the minimum interval to determine cycle frequency.

    Defination of Parameters:
    First Year Dosage in mg: The calculated total quantity of medication in mg administered specifically during the initial 12-month (52-week) period of treatment. This calculation **must strictly adhere to the 'Duration of Treatment' as the upper limit**. If the 'Recommended Dosage' contains a range, calculate the minimum and maximum yearly dosages and present the output as a string in the format 'MIN_VALUE to MAX_VALUE'.
    Number of Doses in First Year: The calculated total number of individual doses administered during the initial 12-month (52-week) period of treatment. This calculation **must strictly adhere to the 'Duration of Treatment' as the upper limit**.

    provide output in following json format:
    ```
    [
        {
        "First Year Dosage in mg": "",
        "Number of Doses in First Year": "",
        }
    ]
    ```

    Notes:
    -Use the information provided If no relavant information then mention as N/A in the particular key of JSON output.
    -Always populate the JSON fields with values or "N/A". Do not leave it empty or return an empty list.
    -If tool call is required then don't provide expression with units like "weeks", "mg", "doses/week" etx. always tool call expression should be a math expression.
    -Don't provide values in decimal points. round off decimal value to nearest integer. example: 2.42 to 2 etc.
    """

    query1="""
    You are an expert medical data processor and calculator.
    Your task is to calculate the 'Subsequent Year Dosage in mg', 'Number of Doses in Subsequent Years' in respective unit based on the 'Recommended Dosage': 1200 mg once weekly for 4 weeks ,
    'Cycle Duration': 4 weeks (q1w for 4 weeks) and
    'Duration of Treatment':Multiple cycles based on clinical evaluation with at least 50 days between start of cycles.

    Duration of Treatment as Limiting Factor for Subsequent Years:
    - Finite duration (e.g., "4 weeks", "6 months", "N/A" for as-needed): Subsequent year dosage/doses must be "N/A".
    - Finite duration and may be used for extended periods with periodic re-evaluation (e.g., Up to 9 weeks systematically evaluated): Subsequent year dosage/doses must be "N/A" if finite duration concludes in the first year.
    - Continuous duration (e.g., "Continuous", "Extended periods"): Calculate for the full 52 weeks in subsequent years.

    Definition of Parameters:
    - Subsequent Year Dosage in mg: Total mg administered in any year after the first (52 weeks), based on the ongoing regimen. **Must be "N/A" if 'Duration of Treatment' is finite** and concludes in the first year. **If the 'Recommended Dosage' contains a range, calculate the minimum and maximum yearly dosages and present the output as a string in the format 'MIN_VALUE to MAX_VALUE'.**
    - Number of Doses in Subsequent Years: Total individual doses administered in any year after the first (52 weeks), based on the ongoing regimen. **Must be "N/A" if 'Duration of Treatment' is finite** and concludes in the first year.


    provide output in following json format:
    ```
    [
        {
        "Subsequent Year Dosage in mg": "",
        "Number of Doses in Subsequent Years": ""
        }
    ]
    ```

    Notes:
    -Use the information provided If no relavant information then mention as N/A in the particular key of JSON output.
    -Always populate the JSON fields with values or "N/A". Do not leave it empty or return an empty list.
    -If tool call is required then don't provide expression with units like "weeks", "mg", "doses/week" etx. always tool call expression should be a math expression.
    -Don't provide values in decimal points. round off decimal value to nearest integer. example: 2.42 to 2 etc.

    """

    asyncio.run(m_chain(query1, llm))