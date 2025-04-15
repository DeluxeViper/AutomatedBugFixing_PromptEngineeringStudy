#!/usr/bin/env python3
import os
import subprocess
import shutil
from pathlib import Path
import argparse
import re

# --- LangChain imports for text splitting ---
from langchain.text_splitter import CharacterTextSplitter, TokenTextSplitter

# ----------------------------
# Configuration Constants
# ----------------------------
# We assume 128k tokens is roughly 512,000 characters.
CHUNK_CHAR_LIMIT = 512_000  
# Final marker added to the last chunk so that ChatGPT knows when the input is complete.
FINAL_MARKER = "<<<END_OF_INPUT>>>"

# ----------------------------
# Helper Functions
# ----------------------------

def run_command(cmd, cwd=None):
    """Run a shell command and return its output (stdout)."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"Command failed: {cmd}")
        print(result.stderr)
    return result.stdout.strip()

def checkout_defects4j_bug(project: str, version: str, work_dir: Path):
    """Checkout the specific Defects4J project version into work_dir."""
    checkout_cmd = f"defects4j checkout -p {project} -v {version} -w {work_dir}"
    print(f"Checking out project with command: {checkout_cmd}")
    subprocess.run(checkout_cmd, shell=True, check=True)
    print(f"Checked out to {work_dir}")

def query_defects4j(project: str) -> str:
    """Query the Defects4J project for bug info."""
    query_cmd = f'defects4j query -p {project} -q "bug.id,report.id,classes.relevant.src,classes.relevant.test,classes.modified,tests.trigger"'
    print(f"Querying project info with: {query_cmd}")
    output = run_command(query_cmd)
    return output

def parse_field(query_output: str, field_name: str, bug_version: str = "") -> str:
    """
    Extracts a specific field from a multi-line comma-separated Defects4J query output.
    
    Expected CSV fields for each line:
        index 0: Bug version number (e.g., "1")
        index 1: Report ID
        index 2: classes.relevant.src
        index 3: classes.relevant.test
        index 4: classes.modified
        index 5: tests.trigger

    If bug_version is provided, only the first line where the first field equals bug_version is considered.
    Surrounding quotation marks are removed.
    """
    lines = query_output.strip().splitlines()
    selected_line = None
    if bug_version:
        for line in lines:
            fields = line.split(",")
            if len(fields) >= 1 and fields[0].strip() == bug_version:
                selected_line = line
                break
    if not selected_line and lines:
        # Fallback: use the first line if no match is found
        selected_line = lines[0]
    
    parts = selected_line.split(',')
    
    if field_name == "classes.relevant.src":
        if len(parts) > 2:
            return parts[2].strip().strip('"')
        else:
            print("DEBUG: Not enough fields for classes.relevant.src")
            return ""
    elif field_name == "classes.relevant.test":
        if len(parts) > 3:
            return parts[3].strip().strip('"')
        else:
            print("DEBUG: Not enough fields for classes.relevant.test")
            return ""
    elif field_name == "classes.modified":
        if len(parts) > 4:
            return parts[4].strip().strip('"')
        else:
            print("DEBUG: Not enough fields for classes.modified")
            return ""
    elif field_name == "tests.trigger":
        if len(parts) > 5:
            return parts[5].strip().strip('"')
        else:
            print("DEBUG: Not enough fields for tests.trigger")
            return ""
    else:
        return ""

def package_to_path(package_name: str) -> Path:
    """Convert a dot-separated package or class name to a relative file path with a .java extension."""
    parts = package_name.split('.')
    filename = parts[-1] + ".java"
    rel_path = Path(*parts[:-1]) / filename
    return rel_path

def find_and_copy_file(work_dir: Path, package_class: str, target_dir: Path):
    """
    Find the Java file corresponding to package_class in src/main/java or src/test/java,
    then copy it into target_dir.
    """
    print(f"DEBUG: Processing package_class: {package_class}")
    rel_path = package_to_path(package_class)
    print(f"DEBUG: Converted package '{package_class}' to relative path: {rel_path}")
    
    # Lang - src_paths = [work_dir / "src" / "main" / "java", work_dir / "src" / "test" / "java"]
    # Cli / Codec
    src_paths = [work_dir / "src" / "java", work_dir / "src" / "test"]

    # Closure
    # src_paths = [work_dir / "src" , work_dir / "test"]
    # Chart - src_paths = [work_dir / "source" , work_dir / "tests"]
    source_file = None
    for base in src_paths:
        candidate = base / rel_path
        print(f"DEBUG: Checking candidate file: {candidate}")
        if candidate.exists():
            print(f"DEBUG: Found file at {candidate}")
            source_file = candidate
            break
        else:
            print(f"DEBUG: File does not exist at: {candidate}")
    if source_file:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / source_file.name
        print(f"DEBUG: Copying file from {source_file} to {target_file}")
        shutil.copy(source_file, target_file)
        print(f"DEBUG: Successfully copied {source_file} to {target_file}")
    else:
        print(f"DEBUG: File for '{package_class}' not found in expected directories: {src_paths}")

def process_classes(query_output: str, work_dir: Path, target_folder_name="classes_to_feed_to_chatgpt"):
    """
    Copy all relevant source and test files (from "classes.relevant.src" and "classes.relevant.test")
    into the target folder.
    """
    target_dir = work_dir / target_folder_name
    src_field = parse_field(query_output, "classes.relevant.src")
    test_field = parse_field(query_output, "classes.relevant.test")

    src_classes = [cls.strip() for cls in src_field.split(';') if cls.strip()]
    test_classes = [cls.strip() for cls in test_field.split(';') if cls.strip()]

    print("Processing relevant source classes:")
    for cls in src_classes:
        find_and_copy_file(work_dir, cls, target_dir)
    
    print("Processing relevant test classes:")
    for cls in test_classes:
        find_and_copy_file(work_dir, cls, target_dir)

def combine_relevant_files(query_output: str, target_folder: Path, only_modified_and_test: bool = False) -> str:
    """
    Combine the contents of files from the target_folder.
    
    If only_modified_and_test is True, include only the file corresponding to "classes.modified"
    and any test files present in "tests.trigger". Otherwise, include all relevant source and test files,
    labeling them as follows:
      - The file corresponding to "classes.modified" is labeled as CLASS TO MODIFY.
      - The test file(s) corresponding to "tests.trigger" are labeled as TRIGGER TEST, with all the failing 
        test methods (if there are more than one, they are combined).
      - Any other files are labeled as RELEVANT SRC FILE or RELEVANT TEST FILE (if the filename ends with 'Test.java').
    """
    # Extract modified class info.
    mod_class = parse_field(query_output, "classes.modified")
    mod_filename = package_to_path(mod_class).name if mod_class else ""
    
    # Extract trigger test info; there may be multiple tests separated by semicolons.
    trigger_test_field = parse_field(query_output, "tests.trigger")
    test_info_list = []  # list of tuples: (test_filename, failing_method)
    if trigger_test_field:
        for test_entry in trigger_test_field.split(';'):
            test_entry = test_entry.strip()
            if not test_entry:
                continue
            if "::" in test_entry:
                test_class, test_method = test_entry.split("::", 1)
            else:
                test_class = test_entry
                test_method = ""
            test_filename = package_to_path(test_class).name if test_class else ""
            test_info_list.append((test_filename, test_method))
    
    # Build a dictionary mapping each test filename to a list of failing methods.
    test_dict = {}
    for test_filename, test_method in test_info_list:
        if test_filename:
            if test_filename in test_dict:
                test_dict[test_filename].append(test_method)
            else:
                test_dict[test_filename] = [test_method]

    parts = []
    # Iterate over every Java file in the target folder.
    for file in sorted(target_folder.glob("*.java")):
        try:
            code = file.read_text()
        except Exception as e:
            code = f"Error reading file: {e}"
        header = ""
        if file.name == mod_filename:
            header = f"===== CLASS TO MODIFY ({file.name}) =====\n"
        elif file.name in test_dict:
            # Combine multiple failing method names if present.
            methods = ", ".join(test_dict[file.name])
            header = f"===== TRIGGER TEST ({file.name}) - Failing Method: {methods} =====\n"
        else:
            if file.name.endswith("Test.java"):
                header = f"===== RELEVANT TEST FILE ({file.name}) =====\n"
            else:
                header = f"===== RELEVANT SRC FILE ({file.name}) =====\n"
        # If only_modified_and_test flag is true, skip files that are not mod or a test file.
        if only_modified_and_test:
            if file.name != mod_filename and file.name not in test_dict:
                continue
        parts.append(header + code)
    combined = "\n\n".join(parts)
    return combined

def write_prompt_files(work_dir: Path):
    """
    Write out three text files containing the updated ChatGPT prompt templates into work_dir.
    """
    additional_instruction = (
        "IMPORTANT: Only fix the failing test method (from 'tests.trigger') and only modify the file corresponding to 'classes.modified'.\n"
        "I will first provide the input file(s) and then the test file. Do not generate the final fixed code until you have received all parts of the input.\n"
    )
    
    prompts = {
        "zero_shot_prompt.txt": (
            "You are an expert Java developer.\n"
            "You will receive multiple Java files from a buggy project.\n\n"
            "Your goal is:\n"
            "- ✅ Modify only the class labeled with: {class_to_modify}\n"
            "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
            "- ❌ Do NOT modify any other files.\n"
        ),
        "few_shot_prompt.txt": (
            "You are an expert Java developer.\n"
            "Below is an example of a bug and its fix:\n\n"
            "EXAMPLE BUG:\n"
            "public int subtract(int a, int b) {{\n"
            "    // Incorrect logic: adds instead of subtracting\n"
            "    return a + b;\n"
            "}}\n\n"
            "EXAMPLE FIX:\n"
            "public int subtract(int a, int b) {{\n"
            "    // Correct logic: subtracts b from a\n"
            "    return a - b;\n"
            "}}\n\n"
            "Now, you will receive multiple Java files from a buggy project.\n\n"
            "Your goal is:\n"
            "- ✅ Modify only the class labeled with: {class_to_modify}\n"
            "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
            "- ❌ Do NOT modify any other files.\n"
        ),
        "chain_of_thought_prompt.txt": (
            "You are an expert Java developer.\n"
            "You will receive multiple Java files from a buggy project.\n\n"
            "Your goal is:\n"
            "- ✅ Modify only the class labeled with: {class_to_modify}\n"
            "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
            "- ❌ Do NOT modify any other files.\n\n"
            "Think step-by-step about the bug: first, analyze the provided code to determine why the test is failing.\n"
            "Then, explain your reasoning and provide the corrected code for the class labeled {class_to_modify}.\n"
            "Enclose your fixed code between the markers: ---FIXED CODE--- and ---END FIXED CODE---.\n"
        )
    }
    
    # For each template, write a file in work_dir.
    for filename, content in prompts.items():
        prompt_file = work_dir / filename
        with open(prompt_file, 'w') as f:
            f.write(content)
        print(f"Prompt template written to {prompt_file}")

def create_prompt_series(prompt_template: str, combined_text: str, output_folder: Path, chunk_tokens: int = 30000):
    """
    Split the combined_text into chunks of up to `chunk_tokens` tokens using TokenTextSplitter,
    then write each chunk as a text file in output_folder.
    
    For every chunk except the last, append a notice that more input follows.
    For the final chunk, append the final marker (FINAL_MARKER).
    """
    output_folder.mkdir(parents=True, exist_ok=True)
    token_splitter = TokenTextSplitter(model_name="gpt-4o", chunk_size=chunk_tokens, chunk_overlap=0)
    chunks = token_splitter.split_text(combined_text)
    print(f"DEBUG: Combined text split into {len(chunks)} chunk(s) for folder {output_folder}")
    
    for i, chunk in enumerate(chunks):
        # Use replace instead of format to inject the code chunk into the template.
        prompt_text = prompt_template.replace("{text}", chunk)
        if i < len(chunks) - 1:
            prompt_text += ("\n\nIMPORTANT: More input follows. "
                            "Do not generate the final solution until you receive the final marker '<<<END_OF_INPUT>>>'.")
        else:
            prompt_text += f"\n\n{FINAL_MARKER}"
        file_path = output_folder / f"part_{i+1}.txt"
        with open(file_path, "w") as f:
            f.write(prompt_text)
        print(f"DEBUG: Wrote prompt chunk {i+1} to {file_path}")

# ----------------------------
# Main Script
# ----------------------------

def main():
    parser = argparse.ArgumentParser(description="Defects4J Automation Script with Prompt Series Generation")
    parser.add_argument("--project", type=str, required=True, help="Defects4J project name (e.g., Lang)")
    parser.add_argument("--version", type=str, required=True, help="Version id (e.g., 1b for buggy version or 1f for fixed version)")
    parser.add_argument("--workdir", type=str, required=True, help="Working directory where the project will be checked out")
    parser.add_argument("--without_context", type=bool, required=False, help="")
    
    args = parser.parse_args()
    work_dir = Path(args.workdir).resolve()
    
    # 1. Checkout the project version.
    checkout_defects4j_bug(args.project, args.version, work_dir)
    
    # 2. Retrieve and save bug info.
    query_output = query_defects4j(args.project)
    query_file = work_dir / "defects4j_query_output.txt"
    with open(query_file, 'w') as f:
        f.write(query_output)
    print(f"Saved query output to {query_file}")
    
    # 3. Process and copy all relevant source and test files into the target folder.
    process_classes(query_output, work_dir)
    
    # 4. Write prompt template files (for manual reference).
    write_prompt_files(work_dir)
    
    # 5. Combine code from the target folder.
    target_folder = work_dir / "classes_to_feed_to_chatgpt"
    combined_code = combine_relevant_files(query_output, target_folder, args.without_context)
    print("DEBUG: Combined code length:", len(combined_code))
    
    # 6. Create prompt series folders for each prompt type.
    zero_shot_folder = work_dir / "zero_shot_prompt_series"
    few_shot_folder = work_dir / "few_shot_prompt_series"
    cot_folder = work_dir / "chain_of_thought_prompt_series"
    
    # Extract dynamic values from query_output.
    # Assume version string is something like "1b" and we extract the number.
    bug_version_match = re.match(r"(\d+)", args.version)
    bug_version = bug_version_match.group(1) if bug_version_match else ""
    
    mod_class = parse_field(query_output, "classes.modified", bug_version)
    mod_filename = package_to_path(mod_class).name if mod_class else "UNKNOWN_MODIFIED_CLASS"

    trigger_test_full = parse_field(query_output, "tests.trigger", bug_version)
    if "::" in trigger_test_full:
        test_class, failing_method = trigger_test_full.split("::", 1)
    else:
        test_class = trigger_test_full
        failing_method = "UNKNOWN_FAILING_METHOD"
    test_filename = package_to_path(test_class).name if test_class else "UNKNOWN_TEST_FILE"
    
    # Define dynamic zero-shot prompt template.
    zero_shot_prompt_template_for_file_input = (
        "You are an expert Java developer.\n"
        "You will receive multiple Java files from a buggy project.\n\n"
        "Your goal is:\n"
        "- ✅ Modify only the class labeled with: {class_to_modify}\n"
        "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
        "- ❌ Do NOT modify any other files.\n"
    ).format(
        class_to_modify=mod_filename,
        test_file=test_filename,
        failing_method=failing_method
    ) + "\n# 🔁 Reminder:\n- The class to be modified is the one under the header labeled as above.\n- The test method to fix is: " + failing_method + "\n- Respond with ONLY the fixed code for the class."

    print("------------------------------------------------------------")
    print("Dynamic zero-shot prompt template:")
    print("------------------------------------------------------------")
    print(zero_shot_prompt_template_for_file_input)
    # 
    # # Define dynamic few-shot prompt template.
    # few_shot_prompt_template = (
    #     "You are an expert Java developer.\n"
    #     "Below is an example of a bug and its fix:\n\n"
    #     "EXAMPLE BUG:\n"
    #     "public int subtract(int a, int b) {{\n"
    #     "    // Incorrect logic: adds instead of subtracting\n"
    #     "    return a + b;\n"
    #     "}}\n\n"
    #     "EXAMPLE FIX:\n"
    #     "public int subtract(int a, int b) {{\n"
    #     "    // Correct logic: subtracts b from a\n"
    #     "    return a - b;\n"
    #     "}}\n\n"
    #     "Now, you will receive multiple Java files from a buggy project.\n\n"
    #     "Your goal is:\n"
    #     "- ✅ Modify only the class labeled with: {class_to_modify}\n"
    #     "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
    #     "- ❌ Do NOT modify any other files.\n\n"
    #     "Respond with ONLY the corrected code for the class labeled {class_to_modify}, starting from the package declaration, without any markdown formatting."
    # ).format(
    #     class_to_modify=mod_filename,
    #     test_file=test_filename,
    #     failing_method=failing_method
    # )
    # 
    # print("\nDynamic few-shot prompt template:")
    # print(few_shot_prompt_template)
    # 
    # # Define dynamic chain-of-thought prompt template.
    # chain_of_thought_prompt_template = (
    #     "You are an expert Java developer.\n"
    #     "You will receive multiple Java files from a buggy project.\n\n"
    #     "Your goal is:\n"
    #     "- ✅ Modify only the class labeled with: {class_to_modify}\n"
    #     "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
    #     "- ❌ Do NOT modify any other files.\n\n"
    #     "Think step-by-step about the bug: first, analyze the provided code and explain why the specified test is failing.\n"
    #     "Then, provide your reasoning followed by the fixed code for the class labeled {class_to_modify}.\n"
    #     "Enclose your fixed code between the markers: ---FIXED CODE--- and ---END FIXED CODE---.\n\n"
    #     "Respond with ONLY your reasoning and the fixed code for the class, starting from the package declaration, without any markdown formatting."
    # ).format(
    #     class_to_modify=mod_filename,
    #     test_file=test_filename,
    #     failing_method=failing_method
    # )
    # 
    # print("\nDynamic chain-of-thought prompt template:")
    # print(chain_of_thought_prompt_template)

    zero_shot_prompt_template = """
You are an expert Java 8 developer.

You will receive multiple Java files from a buggy project.

⚠️ VERY IMPORTANT:
- ONLY modify the Java file labeled with: ===== CLASS TO MODIFY ({class_to_modify}) =====
- ONLY fix the bug that causes the test to fail: ===== TRIGGER TEST ({test_file}) - Failing Method: {failing_method} =====
- ❌ DO NOT generate any output or analysis until you see the marker: <<<END_OF_INPUT>>>
- 🕓 Wait patiently until <<<END_OF_INPUT>>> is provided. Do NOTHING before that.

CODE FILES BELOW (WAIT FOR <<<END_OF_INPUT>>> BEFORE ACTING):
{{text}}

🧠 Think step-by-step:
1. Review the CLASS TO MODIFY and locate the root cause of the bug.
2. Fix the bug so that the failing test method passes.
3. ✅ Return ONLY the corrected contents of the CLASS TO MODIFY — no markdown, no explanation, just raw code starting from the `package` declaration.
4. Combine all relevant Java files ({class_to_modify} + relevant test files) into one long message
    """.format(
        class_to_modify=mod_filename,
        test_file=test_filename,
        failing_method=failing_method
    )

    few_shot_prompt_template_for_file_input = (
        "You are an expert Java developer.\n"
        "Below is an example of a bug and its fix:\n\n"
        "EXAMPLE BUG:\n"
        "public int subtract(int a, int b) {{ \n"
        "    // Incorrect logic: adds instead of subtracting\n"
        "    return a + b;\n"
        "}} \n\n"
        "EXAMPLE FIX:\n"
        "public int subtract(int a, int b) {{ \n"
        "    // Correct logic: subtracts b from a\n"
        "    return a - b;\n"
        "}} \n\n"
        "Now, you will receive multiple Java files from a buggy project.\n\n"
        "Your goal is:\n"
        "- ✅ Modify only the class labeled with: {class_to_modify}\n"
        "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
        "- ❌ Do NOT modify any other files.\n\n"
        "Respond with ONLY the corrected code for the class labeled {class_to_modify}, starting from the package declaration, without any markdown formatting."
    ).format(
        class_to_modify=mod_filename,
        test_file=test_filename,
        failing_method=failing_method
    )

    print("------------------------------------------------------------")
    print("\n\n ======= Few shot template")
    print("------------------------------------------------------------")
    print(few_shot_prompt_template_for_file_input)

    # ✅ Few-Shot Prompt
    few_shot_prompt_template = """
    You are an expert Java developer.
    Below is an example of a bug and its fix:

    EXAMPLE BUG:
    int add(int a, int b) {{
        return a - b;
    }}

    EXAMPLE FIX:
    int add(int a, int b) {{
        return a + b;
    }}

    You will now receive another buggy Java project. Do NOT generate a fix until you see <<<END_OF_INPUT>>>.

    IMPORTANT:
    - Only modify the Java file labeled with: ===== CLASS TO MODIFY ({class_to_modify}) =====
    - Fix the test method indicated by: ===== TRIGGER TEST ({test_file}) - Failing Method: {failing_method} =====
    - Do NOT modify any other files.
    - Do NOT generate any output until you see <<<END_OF_INPUT>>>

    CODE:
    {{text}}

    - Combine all relevant Java files ({class_to_modify} + relevant test files) into one long message

    """.format(
        class_to_modify=mod_filename,
        test_file=test_filename,
        failing_method=failing_method
    )

    chain_of_thought_prompt_template_for_file_input = (
        "You are an expert Java developer.\n"
        "You will receive multiple Java files from a buggy project.\n\n"
        "Your goal is:\n"
        "- ✅ Modify only the class labeled with: {class_to_modify}\n"
        "- ✅ Fix the bug within the Test file: {test_file} with the Failing Method: {failing_method}\n"
        "- ❌ Do NOT modify any other files.\n\n"
        "Think step-by-step about the bug: first, analyze the provided code and identify why the specified test is failing.\n"
        "Then, explain your reasoning followed by your corrected code for the class labeled {class_to_modify}.\n"
        "Enclose your corrected code between the markers: ---FIXED CODE--- and ---END FIXED CODE---.\n\n"
        "Respond with ONLY your reasoning and the fixed code for the class starting from the package declaration, without any markdown formatting."
    ).format(
        class_to_modify=mod_filename,
        test_file=test_filename,
        failing_method=failing_method
    )

    print("------------------------------------------------------------")
    print("\n\n FILE INPUT ========= Chain of thought prompt template")
    print("------------------------------------------------------------")
    print(chain_of_thought_prompt_template_for_file_input)

    chain_of_thought_prompt_template = """
    You are an expert Java developer.
    You will be given a buggy Java project.

    First, think step by step to identify the bug, then provide a fix — BUT ONLY after you have received all necessary files below.

    IMPORTANT:
    - Only modify the Java file labeled with: ===== CLASS TO MODIFY ({class_to_modify}) =====
    - Fix the test method indicated by: ===== TRIGGER TEST ({test_file}) - Failing Method: {failing_method} =====
    - Do NOT begin analysis until you see <<<END_OF_INPUT>>>
    CODE:
    {{text}}

    Once you see the marker <<<END_OF_INPUT>>>:
    1. Think through the bug logically.
    2. Then present the corrected Java code using this format:

    ---FIXED CODE---
    <your fixed code here>
    ---END FIXED CODE---

    - Combine all relevant Java files ({class_to_modify} + relevant test files) into one long message
    Present your explanation and thought by thought process when generating the fixed code.

    """.format(
        class_to_modify=mod_filename,
        test_file=test_filename,
        failing_method=failing_method
    )
    
    # 7. Create series of prompt text files for each prompt type.
    create_prompt_series(zero_shot_prompt_template, combined_code, zero_shot_folder)
    create_prompt_series(few_shot_prompt_template, combined_code, few_shot_folder)
    create_prompt_series(chain_of_thought_prompt_template, combined_code, cot_folder)

if __name__ == "__main__":
    main()
