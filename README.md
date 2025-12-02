🤖 Recruiter AI Agent (MCP Architecture)

Project Overview

The Recruiter AI Agent is an autonomous command-line interface (CLI) application designed to automate the initial screening phase for job applications. It uses the Gemini API to analyze candidate resumes against a provided job description, then acts as an AI Agent to perform a physical action: sorting the files on the user's operating system.

This project successfully implements the Model-Controller-Planner (MCP) architectural pattern and adheres to all project requirements, emphasizing LLM integration, action execution, and robust human-in-the-loop safety.

🚀 Key Features

Autonomous File Sorting (Agent Action): Physically moves .txt resume files into Interview_Candidates or Rejected_Candidates folders.

Structured Output: Utilizes Gemini's response_schema to guarantee that the LLM output is always valid JSON, preventing runtime errors.

Human-in-the-Loop Safety: Prompts the user for authorization before executing every file move (confirmation for destructive operations).

Decision Override: Allows the user to reject the AI's suggestion, automatically moving the file to the opposite folder and logging the intervention.

API Resilience: Implements an exponential backoff retry mechanism to handle transient API errors and rate-limiting gracefully.

Auditability: Logs all decisions, scores, and system actions to a central logs/agent_audit.log file.

Professional UI: Uses the rich library to provide a clear, color-coded, and interactive terminal interface.

🏗️ Architecture: Model-Controller-Planner (MCP)

This agent is built on the MCP pattern, ensuring clean separation of concerns:

Component

Role

Function in this Project

Planner (P)

Orchestrator/Main Loop. Manages the sequence of operations, handles user input for file paths, and enforces the "Stop/Continue" interruption logic.

The MainRunner class.

Model (M)

The Brain. Communicates with the LLM. Responsible for synthesizing input data (Resume + JD) and generating the structured Plan (the JSON command).

The ModelPlanner class.

Controller (C)

The Executor/Hands. Executes the actions dictated by the Model's JSON command, interfaces with the operating system (shutil.move), and runs the safety checks and logging.

The AgentController class.

⚙️ Project Setup

1. Prerequisites

You must have Python 3.8+ and pip installed.

2. Project Installation

# Create and enter the project directory
mkdir recruiter-agent
cd recruiter-agent

# Install dependencies (from requirements.txt)
pip install google-generativeai python-dotenv rich


3. API Key Configuration

Create a file named .env in the project root and paste your Gemini API Key:

# .env
GEMINI_API_KEY="YOUR_GEMINI_API_KEY_HERE"


4. File System Setup (The Sandbox)

Create the required folders to set up the agent's work environment: (MOCK FOLDERS W/ RESUME IN PLACE ALREADY, REPLACE MOCK RESUMES TO USE YOURSELF)

mkdir data
mkdir data/inbox
mkdir logs


Place your job_description.txt and all test resumes (e.g., pass_full_stack.txt) inside the ./data/inbox directory.

▶️ Usage

Run the Agent:

python agent.py


Follow Prompts: The agent will ask you for the default paths. Simply hit Enter to accept the defaults (./data/inbox and ./data/job_description.txt).

Human-in-the-Loop: For each resume, the agent will pause and display its decision in a panel, asking for authorization:

🤖 Agent Request
──────────────────────────────────────────
File: resume_alice.txt
Score: 92/100
Reason: Excellent MERN stack match...
Action: Move to 📂 Interview_Candidates
──────────────────────────────────────────
Authorize this action? [y/n]:


Approve (y): Executes the move to the suggested folder.

Override (n): The file is automatically moved to the opposite folder, and the intervention is logged.

Stopping: After each action, the agent asks if you wish to continue reviewing. Select n to stop the batch process early.

# Auditing

All actions, including scores, reasons, and user overrides, are recorded in the logs/agent_audit.log file in JSON Lines format for auditing and analysis.
