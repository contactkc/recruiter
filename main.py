import os
import shutil
import json
import datetime
import time
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core import exceptions as api_exceptions
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.panel import Panel
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn

# --- CONFIGURATION & UTILITIES ---
load_dotenv()
console = Console()
LOG_DIR = "logs"
AUDIT_FILE = os.path.join(LOG_DIR, "agent_audit.log")

# --- PLANNER ---
class ModelPlanner:
    """
    Handles all communication with the LLM (Gemini).
    Its responsibility is to turn text data into a structured command (the Plan).
    """
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        
        self.response_schema = {
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

        self.model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": self.response_schema,
            }
        )
        self.MAX_RETRIES = 3

    def generate_plan(self, resume_text, job_desc, filename):
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
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.model.generate_content(prompt)
                
                if not response.text:
                    raise ValueError("LLM returned an empty or blocked response.")
                    
                return json.loads(response.text)

            except (api_exceptions.ResourceExhausted, api_exceptions.ServiceUnavailable) as e:
                if attempt < self.MAX_RETRIES - 1:
                    sleep_time = 2 ** attempt
                    console.print(f"[bold orange]⚠️ API Rate Limit Hit or Service Unavailable. Retrying in {sleep_time}s... (Attempt {attempt + 1}/{self.MAX_RETRIES})[/bold orange]")
                    time.sleep(sleep_time)
                    continue
                else:
                    console.print(f"[bold red]❌ Failed after {self.MAX_RETRIES} attempts due to API limits/unavailability.[/bold red]")
                    raise e

            except Exception as e:
                console.print(f"[bold red]❌ Unexpected LLM Error for {filename}: {type(e).__name__} - {str(e)}[/bold red]")
                
                return {
                    "match_score": 0,
                    "thought_process": f"ERROR: Failed to generate plan due to: {type(e).__name__}. Skipped file.",
                    "command": {"action": "SKIP", "destination_folder": "Rejected_Candidates"}
        }

        return {
            "match_score": 0,
            "thought_process": "CRITICAL: Unknown failure in ModelPlanner.",
            "command": {"action": "SKIP", "destination_folder": "Rejected_Candidates"}
        }

# --- CONTROLLER ---
class AgentController:
    """
    Handles execution of the plan (file operations) and user interaction (CLI/Logging).
    """
    def __init__(self, base_dir):
        self.base_dir = base_dir # e.g., './data'
        os.makedirs(LOG_DIR, exist_ok=True) 

    def log_action(self, filename, action, folder, reason):
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "file": filename,
            "action": action,
            "destination": folder,
            "reason": reason
        }
        
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def execute_plan(self, decision, filename):
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
            final_dest_folder = "Rejected_Candidates" if recommended_folder == "Interview_Candidates" else "Interview_Candidates"
            log_reason = f"USER OVERRIDE: Agent recommended {recommended_folder}, but user manually moved to {final_dest_folder}."
            console.print(f"[bold yellow]   ➡️ User Override! Moving to {final_dest_folder}.[/bold yellow]")
            status_message = f"🟠 Action Executed (User Override): Moved to {final_dest_folder}"

        source_path = os.path.join(self.base_dir, "inbox", filename)
        final_dest_dir_path = os.path.join(self.base_dir, final_dest_folder) 
        final_dest_path = os.path.join(final_dest_dir_path, filename)

        if not os.path.exists(final_dest_dir_path):
            os.makedirs(final_dest_dir_path)
        
        try:
            shutil.move(source_path, final_dest_path)
            console.print(f"[bold green]{status_message}[/bold green]\n")
            self.log_action(filename, command["action"], final_dest_folder, log_reason)
        except Exception as e:
            console.print(f"[bold red]   ❌ System Error: {str(e)}[/bold red]\n")


# --- ORCHESTRATOR ---
class MainRunner:
    """
    Manages the overall workflow, initialization, and the processing loop.
    """
    def __init__(self, api_key):
        self.planner = ModelPlanner(api_key)
        self.controller = None

    def run(self):
        console.print("[bold white on blue]  🤖 RECRUITER AI AGENT STARTING...  [/bold white on blue]\n")

        candidates_dir = Prompt.ask("Enter path to resumes folder", default="./data/inbox")
        job_desc_path = Prompt.ask("Enter path to Job Description file", default="./data/job_description.txt")

        base_dir = os.path.dirname(candidates_dir) 
        if base_dir == '':
            base_dir = '.'
        self.controller = AgentController(base_dir)

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

            decision = None
            try:
                with Progress(
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(bar_width=40),
                    TimeElapsedColumn(),
                    transient=True,
                ) as progress:
                    progress.add_task(description=f"Analyzing {file}...", total=None)
                    decision = self.planner.generate_plan(resume_content, job_description, file)
            except api_exceptions.PermissionDenied as e:
                console.print(f"[bold red]❌ CRITICAL ERROR: API Key Invalid or Permission Denied. Stopping execution. ({e})[/bold red]")
                break
            except Exception as e:
                console.print(f"[bold red]❌ CRITICAL ERROR: API connection failed permanently. Stopping execution. ({e})[/bold red]")
                break
            
            if decision:
                self.controller.execute_plan(decision, file)
            
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
    if not os.getenv("GEMINI_API_KEY"):
        console.print("[bold red]❌ Error: GEMINI_API_KEY is missing in .env file[/bold red]")
        exit(1)
        
    runner = MainRunner(os.getenv("GEMINI_API_KEY"))
    runner.run()