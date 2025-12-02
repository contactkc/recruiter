import os
import shutil
import json
import datetime
from dotenv import load_dotenv
import google.generativeai as genai
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

# --- CONFIG ---
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
console = Console()

if not API_KEY:
    console.print("[bold red]❌ Error: GEMINI_API_KEY is missing in .env file[/bold red]")
    exit(1)

genai.configure(api_key=API_KEY)

# --- STRICT JSON SCHEMA ---
response_schema = {
    "type": "OBJECT",
    "properties": {
        "match_score": {"type": "NUMBER"},
        "thought_process": {"type": "STRING"},
        "command": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["MOVE_FILE", "SKIP"]},
                "destination_folder": {"type": "STRING", "enum": ["Interview_Candidates", "Rejected_Candidates"]}
            },
            "required": ["action", "destination_folder"]
        }
    },
    "required": ["match_score", "thought_process", "command"]
}

# --- INIT MODEL ---
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={
        "response_mime_type": "application/json",
        "response_schema": response_schema,
    }
)

# --- PROMPT ---
def analyze_resume(resume_text, job_desc, filename):
    prompt = f"""
    You are an Expert Technical Recruiter Agent.
    
    JOB DESCRIPTION:
    "{job_desc}"

    CANDIDATE RESUME ({filename}):
    "{resume_text}"

    INSTRUCTIONS:
    1. Analyze the resume fit for the job.
    2. Assign a match score (0-100).
    3. DECISION LOGIC:
       - If Score >= 70: Action is MOVE_FILE to "Interview_Candidates"
       - If Score < 70: Action is MOVE_FILE to "Rejected_Candidates"
    """
    
    response = model.generate_content(prompt)
    return json.loads(response.text)

# --- ACTION INTERPRETER ---
def execute_action(decision, filename, base_dir):
    command = decision["command"]
    
    if command["action"] == "SKIP":
        console.print(f"[yellow]   ⏭️  Skipped {filename}[/yellow]")
        return

    recommended_folder = command["destination_folder"]
    
    console.print(Panel(
        f"[bold]File:[/bold] {filename}\n"
        f"[bold]Score:[/bold] {decision['match_score']}/100\n"
        f"[bold]Reason:[/bold] {decision['thought_process']}\n"
        f"[bold]Action:[/bold] Move to 📂 [cyan]{recommended_folder}[/cyan]",
        title="🤖 Agent Request",
        border_style="cyan"
    ))

    final_dest_folder = recommended_folder
    log_reason = decision["thought_process"]
    status_message = ""
    
    if Confirm.ask("Authorize this action?"):
        status_message = f"✅ Action Executed (Agent Recommended): Moved to {recommended_folder}"
    else:
        if recommended_folder == "Interview_Candidates":
            final_dest_folder = "Rejected_Candidates"
        else:
            final_dest_folder = "Interview_Candidates"
        
        log_reason = f"USER OVERRIDE: Agent recommended {recommended_folder}, but user manually moved to {final_dest_folder}."
        console.print(f"[bold yellow]   ➡️ User Override! Moving to {final_dest_folder}.[/bold yellow]")
        status_message = f"🟠 Action Executed (User Override): Moved to {final_dest_folder}"

    source_path = os.path.join(base_dir, filename)
    final_dest_dir_path = os.path.join(os.path.dirname(base_dir), final_dest_folder) 
    final_dest_path = os.path.join(final_dest_dir_path, filename)

    if not os.path.exists(final_dest_dir_path):
        os.makedirs(final_dest_dir_path)
    
    try:
        shutil.move(source_path, final_dest_path)
        console.print(f"[bold green]{status_message}[/bold green]\n")
        log_action(filename, command["action"], final_dest_folder, log_reason)
    except Exception as e:
        console.print(f"[bold red]   ❌ System Error: {str(e)}[/bold red]\n")

# --- LOGGING ---
def log_action(filename, action, folder, reason):
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "file": filename,
        "action": action,
        "destination": folder,
        "reason": reason
    }

    log_dir = "logs"
    log_file_path = os.path.join(log_dir, "agent_audit.log")

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    with open(log_file_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

# --- MAIN LOOP ---
def main():
    console.print("[bold white on blue]  🤖 RECRUITER AI AGENT STARTING...  [/bold white on blue]\n")

    candidates_dir = Prompt.ask("Enter path to resumes folder", default="./data/inbox")
    job_desc_path = Prompt.ask("Enter path to Job Description file", default="./data/job_description.txt")

    if not os.path.exists(candidates_dir) or not os.path.exists(job_desc_path):
        console.print("[bold red]❌ Error: Directory or Job Description file not found. Ensure paths are correct.[/bold red]")
        return

    with open(job_desc_path, "r") as f:
        job_description = f.read()
    
    files = [f for f in os.listdir(candidates_dir) if f.endswith(".txt")]
    total_files = len(files)
    console.print(f"[dim]Found {total_files} resumes to process...[/dim]\n")
    
    num_processed = 0

    for file in files:
        num_processed += 1
        
        resume_path = os.path.join(candidates_dir, file)
        with open(resume_path, "r") as f:
            resume_content = f.read()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            progress.add_task(description=f"Analyzing {file}...", total=None)
            decision = analyze_resume(resume_content, job_description, file)
        
        execute_action(decision, file, candidates_dir)
        
        if num_processed < total_files:
            console.print("--- Review Status ---")
            
            if not Confirm.ask(f"Continue processing the next resume? ({num_processed}/{total_files} processed)"):
                console.print(f"[bold yellow]🛑 User requested stop. Stopping review after {num_processed} of {total_files} resumes.[/bold yellow]")
                break

    if num_processed == total_files:
        console.print("[bold green]✨ All tasks completed. Agent shutting down.[/bold green]")
    else:
        console.print("[bold green]✨ Agent successfully shut down at user request.[/bold green]")

if __name__ == "__main__":
    main()