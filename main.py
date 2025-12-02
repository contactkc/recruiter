import os
import shutil
import json
import datetime
import time
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core import exceptions as api_exceptions
import asyncio
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Button, Static, RichLog
from textual.containers import Container, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from rich.panel import Panel as RichPanel
from rich.console import Console

# --- CONFIG ---
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
LOG_DIR = "logs"
AUDIT_FILE = os.path.join(LOG_DIR, "agent_audit.log")

if not API_KEY:
    print("❌ Error: GEMINI_API_KEY is missing in .env file")
    exit(1)

genai.configure(api_key=API_KEY)

# --- PLANNER & CONTROLLER ---
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

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={
        "response_mime_type": "application/json",
        "response_schema": response_schema,
    }
)

def analyze_resume(resume_text, job_desc, filename, max_retries=3):
    prompt = f"""You are an Expert Technical Recruiter Agent.
    JOB DESCRIPTION: "{job_desc}"
    CANDIDATE RESUME ({filename}): "{resume_text}"
    INSTRUCTIONS: 1. Analyze the resume fit. 2. Assign a score (0-100). 3. DECISION LOGIC: Score >= 70 -> Interview, Score < 70 -> Rejected."""
    
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            if not response.text:
                raise ValueError("LLM returned an empty or blocked response.")
            return json.loads(response.text)
        except (api_exceptions.ResourceExhausted, api_exceptions.ServiceUnavailable) as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise e
        except Exception as e:
            return {
                "match_score": 0,
                "thought_process": f"ERROR: Failed plan generation due to {type(e).__name__}.",
                "command": {"action": "SKIP", "destination_folder": "Rejected_Candidates"}
            }
    return {
        "match_score": 0,
        "thought_process": "CRITICAL: API failed permanently after retries.",
    }

def log_action(filename, action, folder, reason):
    os.makedirs(LOG_DIR, exist_ok=True)
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "file": filename,
        "action": action,
        "destination": folder,
        "reason": reason
    }
    with open(AUDIT_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

# --- TEXTUAL APPLICATION ---
class AuthorizationScreen(Screen):
    def __init__(self, decision, filename, base_dir, **kwargs):
        super().__init__(**kwargs)
        self.decision = decision
        self.filename = filename
        self.base_dir = base_dir
        self.recommended_folder = decision['command']['destination_folder']

    def compose(self) -> ComposeResult:
        yield Static("🤖 [bold]Agent Authorization Required[/bold]", classes="modal-title")

        panel_content = (
            f"File: {self.filename}\n"
            f"Score: {self.decision['match_score']}/100\n"
            f"RECOMMENDS: Move to {self.recommended_folder}\n\n"
            f"Agent Reasoning:\n{self.decision.get('thought_process', 'No explanation provided by agent.')}"
        )

        yield Static(RichPanel(panel_content, title="Decision", border_style="cyan"), classes="modal-panel")

        yield Container(
            Button("APPROVE", variant="primary", id="btn_approve", classes="modal-button", flat=True),
            Button("OVERRIDE / REJECT", variant="error", id="btn_override", classes="modal-button", flat=True)
        )

    def on_button_pressed(self, event: Button.Pressed):
        try:
            modal_future = getattr(self.app, "_modal_future", None)
            if modal_future is not None and not modal_future.done():
                if event.button.id == "btn_approve":
                    modal_future.set_result("APPROVE")
                elif event.button.id == "btn_override":
                    modal_future.set_result("OVERRIDE")
        except Exception:
            pass
        self.app.pop_screen()

class Recruiter(App[None]):
    CSS = """
    Screen { align: center middle; }
    .title { height: 1; text-align: center; color: #1E90FF; text-style: bold; }
    #log-area { height: 1fr; background: #222222; margin: 1 2; padding: 1; border: double #444444; }
    #controls { height: auto; padding: 0 2; margin-top: 1; }
    Input { width: 100%; height: 3; margin-bottom: 1; }
    Button { min-width: 15; margin-right: 2; }
    
    /* Modal Styling */
    AuthorizationModal {
        width: 60%;
        height: 60%;
        border: thick $primary;
        background: #111111;
        padding: 2;
        align: center middle;
    }
    .modal-title { color: yellow; text-style: bold; height: 1; margin-bottom: 2; }
    .modal-panel { width: 100%; height: auto; }
    .modal-button { width: 45%; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Footer()
        yield Static("[bold #1E90FF]🤖 Recruiter AI Agent[/bold #1E90FF]", classes="title")
        
        with Container(id="controls"):
            yield Input(placeholder="./data/job_description.txt", id="input_jd_path")
            yield Input(placeholder="./data/inbox (Resumes Folder)", id="input_resume_dir")
            yield Button("START ANALYSIS", variant="primary", id="btn_start", flat=True)
            yield Button("STOP AGENT", variant="error", id="btn_stop", disabled=True, flat=True)
        
        yield RichLog(id="log-area", auto_scroll=True, wrap=True)
        
        self.job_desc = ""
        self.candidates_dir = ""
        self.files_to_process = []
        self.num_processed = 0
        self.processing_task = None

    def log_message(self, message):
        self.log_widget.write(f"{datetime.datetime.now().strftime('%H:%M:%S')} {message}")

    def on_mount(self):
        self.log_widget = self.query_one("#log-area", RichLog)
        self.title = ""
        
        self.query_one("#input_jd_path", Input).value = "./data/job_description.txt"
        self.query_one("#input_resume_dir", Input).value = "./data/inbox"
        self.log_message("Recruiter Ready. Enter paths and press START.")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_start":
            self.start_processing()
        elif event.button.id == "btn_stop":
            self.stop_processing("User manually stopped the agent.")

    def start_processing(self):
        self.job_desc_path = self.query_one("#input_jd_path", Input).value
        self.candidates_dir = self.query_one("#input_resume_dir", Input).value
        
        if not os.path.exists(self.candidates_dir) or not os.path.exists(self.job_desc_path):
            self.log_message("❌ ERROR: One or both paths are invalid. Check paths.")
            return

        try:
            with open(self.job_desc_path, "r") as f:
                self.job_desc = f.read()
            
            self.files_to_process = [f for f in os.listdir(self.candidates_dir) if f.endswith(".txt")]
            self.num_processed = 0
            
            if not self.files_to_process:
                self.log_message("⚠️ No .txt resume files found in inbox.")
                return

            self.log_message(f"-- Starting Batch -- Found {len(self.files_to_process)} resumes.")
            self.query_one("#btn_start", Button).disabled = True
            self.query_one("#btn_stop", Button).disabled = True

            self.processing_task = asyncio.create_task(self.process_resumes())

        except Exception as e:
            self.log_message(f"❌ CRITICAL READ ERROR: {str(e)}")
            self.query_one("#btn_start", Button).disabled = False

    def stop_processing(self, reason):
        if self.processing_task:
            self.processing_task.cancel()
        self.log_message(f"🛑 {reason}")
        self.query_one("#btn_start", Button).disabled = False
        self.query_one("#btn_stop", Button).disabled = True
    
    async def process_resumes(self):
        self.query_one("#btn_stop", Button).disabled = False

        while self.num_processed < len(self.files_to_process):
            filename = self.files_to_process[self.num_processed]
            self.log_message(f"\n--- Processing File {self.num_processed + 1}/{len(self.files_to_process)}: {filename} ---")
            
            resume_path = os.path.join(self.candidates_dir, filename)
            try:
                with open(resume_path, "r") as f:
                    resume_content = f.read()
            except Exception as e:
                self.log_message(f"❌ Read Error: Skipping {filename} ({str(e)})")
                self.num_processed += 1
                continue
                
            self.log_message(f"🧠 Thinking... Calling Gemini API.")
            decision = await asyncio.to_thread(analyze_resume, resume_content, self.job_desc, filename)
            
            if decision['command']['action'] == "SKIP":
                self.log_message(f"❌ API ERROR: Skipped {filename}. Reason: {decision['thought_process']}")
                log_action(filename, "SKIP", "N/A", decision['thought_process'])
                self.num_processed += 1
                continue
            self.log_message(f"Agent recommends: {decision['command'].get('destination_folder')} — Reason: {decision.get('thought_process','(no explanation)')}")
            
            try:
                loop = asyncio.get_running_loop()
                self._modal_future = loop.create_future()

                await self.push_screen(AuthorizationScreen(decision, filename, self.candidates_dir))

                try:
                    action = await self._modal_future
                finally:
                    if hasattr(self, "_modal_future"):
                        delattr(self, "_modal_future")

                self.log_message(f"Decision Recommendation: {decision['command'].get('destination_folder')} | User Action: {action}")

                self.execute_file_move(decision, filename, action)
                
            except Exception as e:
                self.log_message(f"❌ EXECUTION ERROR: {str(e)}")
                self.num_processed += 1
                continue
            
            self.num_processed += 1

        self.log_message("✅ Batch Finished! All files processed.")
        self.call_after_refresh(lambda: self.stop_processing("Batch completed."))

    def execute_file_move(self, decision, filename, user_action):
        base_dir = os.path.dirname(self.candidates_dir) 
        if base_dir == '': base_dir = '.'

        recommended_folder = decision['command']['destination_folder']
        final_dest_folder = recommended_folder
        log_reason = decision['thought_process']
        
        if user_action == "OVERRIDE":
            final_dest_folder = "Rejected_Candidates" if recommended_folder == "Interview_Candidates" else "Interview_Candidates"
            log_reason = f"USER OVERRIDE: Agent recommended {recommended_folder}, user moved to {final_dest_folder}. Agent Reason: {log_reason}"
            self.log_message(f"➡️ USER OVERRIDE! Moving to {final_dest_folder}.")
        elif user_action == "APPROVE":
            self.log_message(f"✅ APPROVED: Moving to {recommended_folder}.")
        
        source_path = os.path.join(self.candidates_dir, filename)
        final_dest_dir_path = os.path.join(base_dir, final_dest_folder)
        final_dest_path = os.path.join(final_dest_dir_path, filename)
        
        try:
            if not os.path.exists(final_dest_dir_path):
                os.makedirs(final_dest_dir_path)
            
            shutil.move(source_path, final_dest_path)
            log_action(filename, decision['command']['action'], final_dest_folder, log_reason)
            self.log_message(f"✅ File moved successfully to 📂 {final_dest_folder}.")
        except Exception as e:
            self.log_message(f"❌ System Error Moving File: {str(e)}")


if __name__ == "__main__":
    app = Recruiter()
    app.run()