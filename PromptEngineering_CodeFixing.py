import os
from dotenv import load_dotenv
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

load_dotenv()

os.environ['OPENAI_API_KEY'] = ''


ZERO_SHOT_PROMPT = PromptTemplate(
    template="""You are an expert Python developer.
Below is a piece of code that has a bug (syntax or logical). Please fix it.

CODE:
{text}

Provide ONLY the corrected code (no additional explanation).""",
    input_variables=["text"]
)

FEW_SHOT_PROMPT = PromptTemplate(
    template="""You are an expert Python developer.
Below is an example of a bug and its fix:

EXAMPLE BUG:
def add_numbers(a, b):
    return a - b

EXAMPLE FIX:
def add_numbers(a, b):
    return a + b

Now, here is another buggy code snippet. Fix it using the same logic.

CODE:
{text}

Provide ONLY the corrected code (no extra text).""",
    input_variables=["text"]
)

CHAIN_OF_THOUGHT_PROMPT = PromptTemplate(
    template="""You are an expert Python developer.
I will give you code with a bug. Think step by step about the bug,
explain your reasoning, then provide a corrected version.

CODE:
{text}

First, explain your reasoning (step-by-step), then show the fixed code.""",
    input_variables=["text"]
)

def read_python_file(file_path):
    with open(file_path, "r") as f:
        code = f.read()
    return code

def fix_code_with_prompts(file_path, llm):
    code = read_python_file(file_path)
    results = {}
    for prompt_name, prompt_template in [
            ("zero_shot", ZERO_SHOT_PROMPT),
            ("few_shot", FEW_SHOT_PROMPT),
            ("chain_of_thought", CHAIN_OF_THOUGHT_PROMPT)
        ]:
        prompt = prompt_template.format(text=code)
        response = llm(prompt)
        if hasattr(response, "content"):
            fixed_code = response.content
        else:
            fixed_code = response  
        results[prompt_name] = fixed_code
    return results

llm = ChatOpenAI(model_name="gpt-3.5-turbo", temperature=0)

results = fix_code_with_prompts("/Users/mazen_wael/Downloads/Codes_Master:Practice/Master's_Research/bug.py", llm)

for prompt_type, fixed_code in results.items():
    print(f"Prompt Type: {prompt_type}")
    print("Fixed Code:")
    print(fixed_code)
    print("\n" + "="*50 + "\n")






