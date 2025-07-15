from platform import node
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from customtkinter import CTkImage
from datetime import datetime
from PIL import Image, ImageOps
import uuid
import regex as re
import requests
import textwrap
import base64
import tkinter.filedialog as filedialog
import hashlib
import os
from google.cloud.vision_v1 import AnnotateImageResponse, EntityAnnotation
import threading
import logging
import re
import pytesseract
from PIL import ImageEnhance
import easyocr
from google.cloud import vision
import io
from PIL import Image as PILImage
from PIL import ImageTk
from dotenv import load_dotenv
import time
from PIL import Image
from io import BytesIO
from requests.auth import HTTPBasicAuth
import json

load_dotenv()
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://localhost:5678")
N8N_API_KEY = os.getenv("N8N_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AVAILABLE_OLLAMA_MODELS = [
    "llava:13b", "deepseek-coder:6.7b", "deepseek-r1:7b", "llava:latest", "llama2:latest"
]

class N8nWorkflowGenerator:
    def __init__(self, ollama_url, logger=None, message_callback=None):
            self.ollama_url = ollama_url
            self.log = logger or logging.getLogger(__name__)
            self.log_message = message_callback or (lambda msg: self.log.info(msg))
            # Node knowledge base for type mapping
            self.node_knowledge_base = [
                {"name": "Webhook", "type": "n8n-nodes-base.webhook", "keywords": ["webhook", "http", "callback", "api", "receive"], "description": "Trigger via HTTP"},
                {"name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "keywords": ["manual", "start", "begin", "test"], "description": "Manual start"},
                {"name": "Cron", "type": "n8n-nodes-base.cron", "keywords": ["cron", "schedule", "timer", "interval", "every"], "description": "Scheduled trigger"},
                {"name": "IF", "type": "n8n-nodes-base.if", "keywords": ["if", "condition", "branch", "decision", "yes", "no"], "description": "Conditional branch"},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "keywords": ["switch", "case", "route", "option", "multi"], "description": "Multi-branch"},
                {"name": "Set", "type": "n8n-nodes-base.set", "keywords": ["set", "assign", "value", "data", "field"], "description": "Set data"},
                {"name": "Code", "type": "n8n-nodes-base.code", "keywords": ["code", "js", "javascript", "custom", "logic"], "description": "Custom JS"},
                {"name": "HTTP Request", "type": "n8n-nodes-base.httpRequest", "keywords": ["http", "request", "api", "call", "fetch", "post", "get"], "description": "HTTP/API call"},
                {"name": "OpenAI", "type": "@n8n/n8n-nodes-langchain.openAi", "keywords": ["ai", "openai", "gpt", "chatgpt", "llm"], "description": "OpenAI LLM"},
                {"name": "Notion", "type": "n8n-nodes-base.notion", "keywords": ["notion", "note", "database", "page"], "description": "Notion integration"},
                {"name": "Google Sheets", "type": "n8n-nodes-base.googleSheets", "keywords": ["sheet", "google", "spreadsheet", "excel"], "description": "Google Sheets"},
                {"name": "SplitInBatches", "type": "n8n-nodes-base.splitInBatches", "keywords": ["split", "batch", "chunk", "divide"], "description": "Batch processing"},
                {"name": "Merge", "type": "n8n-nodes-base.merge", "keywords": ["merge", "combine", "join", "union"], "description": "End/No operation"}]

    def generate_workflow_pipeline(self, image_path, user_prompt):
        self.log.info("🚀 Starting full pipeline for image: %s", image_path)
        image_description = self.analyze_image_with_llava(image_path)
        self.log.info("🧠 Image analyzed: %s", image_description[:500])
        final_logic = self.refine_logic_with_phi3(image_description, user_prompt)
        self.log.info("🧠 Final logic:%s", final_logic.strip())
        raw_json = self.generate_n8n_json_with_deepseek(final_logic)
        workflow = self.extract_json_from_response(raw_json)
        if workflow:
            self.log.info("✅ JSON extracted successfully")
            if not self.validate_workflow_structure(workflow):
                self.log.error("❌ Workflow structure is invalid.")
                return self.fallback_workflow()
            workflow = self.validate_and_fix_workflow(workflow)
            workflow = self.fix_connections(workflow)
            if not self.validate_workflow_structure(workflow):
                self.log.error("❌ Final structure is invalid after mapping/fix.")
                return self.fallback_workflow()
            self.log.debug("📦 Final workflow structure:%s", json.dumps(workflow, indent=2))
            return workflow
        else:
            self.log.warning("❌ Workflow generation failed or invalid format.")
            return None

    
    def validate_workflow_schema(self, workflow: dict) -> tuple[bool, str]:
        if not isinstance(workflow, dict):
            return False, "Workflow is not a dictionary."

        if "nodes" not in workflow:
            return False, "Missing 'nodes' key in workflow."

        if not isinstance(workflow["nodes"], list):
            return False, "'nodes' must be a list."

        if "connections" not in workflow:
            workflow["connections"] = {}  # ➤ แก้ตรงนี้ให้อัตโนมัติ
            self.log.info("ℹ️ Added empty 'connections' to workflow.")

        for i, node in enumerate(workflow["nodes"], start=1):
            for field in ["id", "name", "type", "position"]:
                if field not in node:
                    return False, f"Node {i} is missing '{field}'."

        return True, "Workflow is valid."
    def wrap_nodes_array_as_workflow(self, data):
        if isinstance(data, list):
            return {
                "name": "Wrapped Workflow",
                "nodes": data,
                "connections": {}
            }
        return data
    
    def normalize_node_types(self, workflow):
        type_map = {
            "ManualTrigger": "n8n-nodes-base.manualTrigger",
            "Webhook": "n8n-nodes-base.webhook",
            "Set": "n8n-nodes-base.set",
            "If": "n8n-nodes-base.if",
            "HTTP Request": "n8n-nodes-base.httpRequest",
            "OpenAI": "n8n-nodes-base.openai",
            # เพิ่มเติมได้เรื่อยๆตามที่ LLM ชอบตอบ
        }

        for node in workflow.get("nodes", []):
            if "type" in node and node["type"] in type_map:
                node["type"] = type_map[node["type"]]

            # ✅ แก้ id ซ้ำกับ type (LLM ชอบตั้ง id = type)
            if node.get("id", "").startswith("n8n-nodes-base."):
                node["id"] = str(uuid.uuid4())

        return workflow

    
    def normalize_nodes(self, workflow):
        """
        Normalizes node types and names and syncs those changes in the connections.
        """
        self.log.debug("🧰 Normalizing nodes...")

        if not workflow or "nodes" not in workflow:
            self.log.warning("⚠️ No nodes found to normalize.")
            return

        nodes = workflow["nodes"]
        connections = workflow.get("connections", {})

        # Example type mapping: from old or generic types → n8n-supported
        type_mapping = {
            "placeholder": "n8n-nodes-base.noOp",
            "decision": "n8n-nodes-base.if",
            "code": "n8n-nodes-base.code",
            "set": "n8n-nodes-base.set",
            "http": "n8n-nodes-base.httpRequest",
            "webhook": "n8n-nodes-base.webhook",
            "manualTrigger": "n8n-nodes-base.manualTrigger",
            "trigger": "n8n-nodes-base.manualTrigger",
            "end": "n8n-nodes-base.noOp",
        }

        name_map = {}
        updated_nodes = []

        for node in nodes:
            old_type = node.get("type")
            name = node.get("name", "")
            node_id = node.get("id", "")

            # Fix missing required fields
            node.setdefault("parameters", {})
            node.setdefault("typeVersion", 1)
            node.setdefault("position", [0, 0])
            node.setdefault("name", old_type or "Unnamed")
            node.setdefault("id", name.replace(" ", "_").lower())

            # Apply type mapping
            if old_type in type_mapping:
                new_type = type_mapping[old_type]
                self.log.debug(f"🔄 Mapping node '{name}' from {old_type} → {new_type}")
                node["type"] = new_type

            # Keep track of name mapping (id → name)
            name_map[node_id] = node["name"]
            updated_nodes.append(node["name"])

        # Replace keys in connections
        updated_connections = {}
        for from_node_name, conn_data in connections.items():
            new_from_name = name_map.get(from_node_name, from_node_name)
            updated_connections[new_from_name] = {}

            for branch_key, branches in conn_data.items():
                updated_connections[new_from_name][branch_key] = []

                for branch in branches:
                    if not isinstance(branch, list):
                        branch = [branch]

                    updated_branch = []
                    for conn in branch:
                        if isinstance(conn, dict) and "node" in conn:
                            old_to = conn["node"]
                            new_to = name_map.get(old_to, old_to)
                            conn["node"] = new_to
                            updated_branch.append(conn)
                    if updated_branch:
                        updated_connections[new_from_name][branch_key].append(updated_branch)

        workflow["connections"] = updated_connections

        self.log.debug("🔧 Node types after normalization: %s", [n["type"] for n in nodes])
        self.log.debug("🔧 Node names after normalization: %s", updated_nodes)
        self.log.debug("🔗 Connections after remapping: %s", json.dumps(workflow["connections"], indent=2))

        return workflow

    def load_example_workflows(self):
        import os
        import json

        examples_dir = "examples"
        examples = []

        if not os.path.exists(examples_dir):
            self.log.warning("⚠️ No 'examples/' directory found.")
            return examples

        for file in os.listdir(examples_dir):
            if file.endswith(".json"):
                try:
                    with open(os.path.join(examples_dir, file), "r", encoding="utf-8") as f:
                        data = json.load(f)
                        examples.append(data)
                except Exception as e:
                    self.log.warning(f"⚠️ Failed to load {file}: {e}")

        return examples
   

    def fallback_workflow(self) -> dict:
        self.log.info("⚠️ Falling back to minimal workflow structure.")
        return {
            "name": "Fallback workflow",
            "nodes": [{
                "id": str(uuid.uuid4()),
                "name": "Manual Trigger",
                "type": "n8n-nodes-base.manualTrigger",
                "typeVersion": 1,
                "position": [100, 200],
                "parameters": {}
            }],
            "connections": {},
            "settings": {}
        }
        
    def call_ollama_generate(self, model, prompt, images=None):
        url = "http://localhost:11434/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        if images:
            payload["images"] = images
        try:
            response = requests.post(url, json=payload, timeout=800)
            response.raise_for_status()
            result = response.json()
            return result.get("response", "").strip()
        except Exception as e:
            self.log.error(f"❌ Failed calling Ollama API: {e}")
            return None

    def analyze_image_with_llava(self, image_path):
        self.log.info("🔍 Analyzing image with LLaVA: %s", image_path)
        image_b64 = self.image_to_base64(image_path)
        prompt = (
            "Analyze this image of a flowchart and extract the steps, decisions, labels, and connections "
            "in logical order. Describe them as text."
        )
        payload = {
            "model": "llava:13b",
            "prompt": prompt,
            "stream": False,
            "images": [image_b64]
        }
        try:
            response = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=120)
            result = response.json()
            text = result.get('response', '').strip()
            # If LLaVA result is too short or looks like a failure, fallback to OCR
            if not text or len(text) < 30 or "unreadable" in text.lower():
                raise Exception("LLaVA output too short or failed")
            return text
        except Exception as e:
            self.log.warning(f"LLaVA failed: {e}, fallback to OCR")
            # --- Preprocess image for OCR ---
            try:
                img = Image.open(image_path)
                # Grayscale
                gray = img.convert('L')
                # Resize (enlarge)
                large = gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)
                # Enhance contrast
                enhancer = ImageEnhance.Contrast(large)
                contrast = enhancer.enhance(2.0)
                # Threshold
                binary = contrast.point(lambda p: 255 if p > 130 else 0)
                # Try pytesseract
                text = pytesseract.image_to_string(binary, lang='eng')
                if text.strip() and len(text.strip()) > 10:
                    return "Extracted via OCR:\n" + text
                # Fallback to easyocr if pytesseract fails
                try:
                    reader = easyocr.Reader(['en'])
                    result = reader.readtext(image_path, detail=0)
                    text_easy = "\n".join(result)
                    if text_easy.strip():
                        return "Extracted via EasyOCR:\n" + text_easy
                except Exception as e2:
                    self.log.warning(f"EasyOCR failed: {e2}")
                return "No text could be extracted from image"
            except Exception as ocr_e:
                self.log.warning(f"OCR fallback failed: {ocr_e}")
                return "No text could be extracted from image"

    def create_fallback_n8n_workflow(self, data=None, reason="unknown"):
        return {
            "name": f"Fallback workflow ({reason})",
            "nodes": [],
            "connections": {}
        }

    def refine_logic_with_phi3(self, image_text: str, user_description: str = "") -> str | None:
        self.log.info("🔁 Refining logic with Phi-3")

        prompt = f"""
    You are a reasoning assistant for flowchart interpretation.

    Given the following image description and user input, write a clear logic flow that can be converted into an n8n automation.
    Use numbered steps, no explanation, no extra text.
    NEVER apologize. NEVER say "I'm sorry". Just return the logic steps.

    Image Description:
    {image_text.strip()}

    User Notes (optional):
    {user_description.strip() or "[None]"}
    """

        try:
            response = self.call_ollama_generate(model="phi3:instruct", prompt=prompt)
        except Exception as e:
            self.log.error("❌ Failed calling Ollama API: %s", str(e))
            return None

        if not response:
            self.log.warning("⚠️ Phi-3 returned empty or null response.")
            return None

        try:
            with open("phi3_raw_response.txt", "w", encoding="utf-8") as f:
                f.write(response)
            self.log.debug("💬 Phi-3 raw response saved to phi3_raw_response.txt")
        except Exception as e:
            self.log.warning("⚠️ Failed to save phi3 response: %s", str(e))

        return response.strip()




    def extract_important_steps(self, logic_text):
        """
        เลือกเฉพาะ step หรือหัวข้อที่สำคัญจาก logic_text
        เช่น เลือกเฉพาะบรรทัดที่มี keyword สำคัญ หรือเป็น numbered step
        """
        if not logic_text:
            return ""
        keywords = [
            "input", "validate", "api", "decision", "output", "send", "process",
            "check", "call", "request", "webhook", "trigger", "user", "form", "database"
        ]
        lines = [line for line in logic_text.split('\n') if line.strip()]
        important = [
            line for line in lines
            if any(kw in line.lower() for kw in keywords)
            or re.match(r"^\d+[\.\)]", line.strip())  # เลือก numbered step ด้วย
        ]
        # ถ้าไม่เจออะไรเลย ให้ return logic_text เดิม
        return "\n".join(important) if important else logic_text

    def generate_n8n_json_with_deepseek(self, logic_text, model_name="deepseek-coder:6.7b"):
        self.log.info("🧠 Generating JSON with DeepSeek-Coder")

        # ตัวอย่าง workflow ที่ดี (ใช้เป็นตัวอย่างใน prompt)
        example_workflow = {
            "name": "Example Workflow",
            "nodes": [
                {
                    "id": "1",
                    "name": "Manual Trigger",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "position": [100, 200],
                    "parameters": {}
                },
                {
                    "id": "2",
                    "name": "Process Step",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [500, 200],
                    "parameters": {
                        "language": "javascript",
                        "jsCode": "// process logic\nreturn items;"
                    }
                }
            ],
            "connections": {
                "Manual Trigger": {
                    "main": [[{"node": "Process Step", "type": "main", "index": 0}]]
                }
            }
        }

        prompt = f"""
    You are an expert n8n workflow developer.
    Given the following logic steps, generate a valid n8n workflow as a JSON object.
    - Each step or decision must be a separate node.
    - Connect the nodes in the correct order using the "connections" field.
    - Do not invent node types. 
    - Use only these node types: manualTrigger, webhook, code, set, if, httpRequest, emailSend, googleSheets, notion, merge, switch, wait.
    - Each node must have: id, name, type, typeVersion, position, parameters.
    - Output ONLY valid JSON. No markdown, no explanation, no comments.
    - If you cannot generate a valid workflow, output an empty JSON object: {{}}

    Example:
    {json.dumps(example_workflow, indent=2)}

    Now, generate the n8n workflow JSON for this process:
    {logic_text}
    """

        response_text = self.call_ollama_generate(model=model_name, prompt=prompt)
        self.log.info("📄 LLM response preview (first 500 chars):\n%s", response_text[:500])

        # ตรวจสอบและ extract JSON
        workflow = self.extract_json_from_response(response_text)
        if workflow:
            self.log.info("✅ JSON extracted successfully")
            return workflow
        else:
            self.log.warning("⚠️ Falling back to minimal workflow structure.")
            return self.fallback_workflow()
            
    def validate_workflow_structure(self, workflow):
        self.log.debug("🔍 Validating workflow structure...")

        if not isinstance(workflow, dict):
            self.log.warning("❌ Workflow must be a dictionary.")
            return False

        required_keys = ["name", "nodes", "connections"]
        for key in required_keys:
            if key not in workflow:
                self.log.warning(f"❌ Missing required key: {key}")
                return False

        if not isinstance(workflow["nodes"], list) or not workflow["nodes"]:
            self.log.warning("❌ `nodes` must be a non-empty list.")
            return False

        # Normalize node names for comparison
        node_names = [node.get("name") for node in workflow["nodes"] if "name" in node]
        node_names_norm = [n.strip().lower() for n in node_names]

        for node in workflow["nodes"]:
            for field in ["id", "name", "type", "position", "typeVersion", "parameters"]:
                if field not in node:
                    self.log.warning(f"❌ Node missing required field '{field}': {node}")
                    return False

        if not isinstance(workflow["connections"], dict):
            self.log.warning("❌ `connections` must be a dictionary.")
            return False

        for from_node, conn in workflow["connections"].items():
            from_node_norm = from_node.strip().lower()
            if from_node_norm not in node_names_norm:
                self.log.warning(f"❌ Connection from unknown node: {from_node}")
                return False
            if "main" not in conn or not isinstance(conn["main"], list):
                self.log.warning(f"❌ Connection for '{from_node}' missing valid 'main' list.")
                return False
            for branch in conn["main"]:
                if not isinstance(branch, list):
                    self.log.warning(f"❌ Branch in connection of '{from_node}' is not a list.")
                    return False
                for conn_obj in branch:
                    if not isinstance(conn_obj, dict) or "node" not in conn_obj:
                        self.log.warning(f"❌ Invalid connection object in '{from_node}': {conn_obj}")
                        return False
                    target_node_norm = conn_obj["node"].strip().lower()
                    if target_node_norm not in node_names_norm:
                        self.log.warning(f"❌ Connection to unknown node: {conn_obj['node']}")
                        return False

        self.log.info("✅ Workflow structure validated.")
        return True

    
    def get_valid_node_types(self):
        """
        Return a set of all valid node types from the node knowledge base.
        """
        # ใช้ knowledge base เดียวกันทั้งสองคลาส
        if hasattr(self, "node_knowledge_base"):
            return set(node["type"] for node in self.node_knowledge_base)
        # fallback (กรณี N8nWorkflowGenerator)
        return {
            "n8n-nodes-base.manualTrigger", "n8n-nodes-base.webhook", "n8n-nodes-base.cron",
            "n8n-nodes-base.set", "n8n-nodes-base.code", "n8n-nodes-base.if", "n8n-nodes-base.switch",
            "n8n-nodes-base.merge", "n8n-nodes-base.wait", "n8n-nodes-base.noOp", "n8n-nodes-base.httpRequest",
            "n8n-nodes-base.emailSend", "n8n-nodes-base.googleSheets", "n8n-nodes-base.notion",
            "n8n-nodes-base.airtable", "n8n-nodes-base.mySql", "n8n-nodes-base.postgres",
            "n8n-nodes-base.slack", "n8n-nodes-base.line", "n8n-nodes-base.telegram", "n8n-nodes-base.discord",
            "n8n-nodes-base.facebookMessenger", "n8n-nodes-base.dropbox", "n8n-nodes-base.googleDrive",
            "@n8n/n8n-nodes-langchain.openAi", "@n8n/n8n-nodes-langchain.anthropic",
            "@n8n/n8n-nodes-langchain.chatTrigger", "@n8n/n8n-nodes-langchain.textClassifier",
            "@n8n/n8n-nodes-langchain.informationExtractor", "@n8n/n8n-nodes-langchain.agent",
            "@n8n/n8n-nodes-langchain.lmChatOllama"
        }

    def robust_suggest_node_type(self, node_type_raw, node_name=""):
        """
        Map node type from LLM or user to a valid n8n node type using knowledge base and heuristics.
        """
        node_type_raw = (node_type_raw or "").strip().lower()
        # 1. ตรงกับ knowledge base
        for node in self.node_knowledge_base:
            if node_type_raw == node["type"].lower():
                return node["type"]
        # 2. ตรงกับชื่อ node หรือ keyword
        for node in self.node_knowledge_base:
            if node_type_raw in node["name"].lower() or node_type_raw in [kw.lower() for kw in node["keywords"]]:
                return node["type"]
        # 3. ใช้ suggest_node_type จากชื่อ node
        if hasattr(self, "suggest_node_type"):
            return self.suggest_node_type(node_name)
        # 4. fallback
        return "n8n-nodes-base.code"

    def validate_and_fix_workflow(self, workflow):
        import uuid

        # --- Normalize node types and fields ---
        fixed_nodes = []
        name_map = {}
        id_map = {}

        for i, node in enumerate(workflow.get("nodes", [])):
            node_type = self.robust_suggest_node_type(node.get("type"), node.get("name", ""))
            if node_type not in self.get_valid_node_types():
                self.log_message(f"⚠️ Skipping unsupported node type: {node.get('type')} ({node.get('name', node.get('id', 'no-name'))})")
                continue

            node["type"] = node_type
            node.setdefault("id", str(uuid.uuid4()))
            node.setdefault("name", node.get("name", node_type))
            node.setdefault("typeVersion", 1)
            node.setdefault("parameters", {})
            node.setdefault("position", [i * 400 + 100, 200])

            # Map ทั้ง id และ name เป็น lower case
            name_map[node["name"].strip().lower()] = node["id"]
            id_map[node["id"]] = node["name"].strip().lower()
            fixed_nodes.append(node)

        if not fixed_nodes:
            self.log_message("⚠️ No valid nodes found. Returning fallback workflow.")
            return self.create_fallback_n8n_workflow({}, "no_valid_nodes")

        # --- Normalize connections ---
        connections = workflow.get("connections", {})
        fixed_connections = {}

        node_names_lower = set(n["name"].strip().lower() for n in fixed_nodes)
        node_ids = set(n["id"] for n in fixed_nodes)

        for from_node, conn_data in connections.items():
            # รองรับทั้ง id และ name
            from_node_norm = from_node.strip().lower()
            if from_node in node_ids:
                from_node_name = id_map[from_node]
            else:
                from_node_name = from_node_norm

            if from_node_name not in node_names_lower:
                self.log_message(f"⚠️ Skipping connection from unknown node: {from_node}")
                continue

            # ใช้ name เป็น key เสมอ
            fixed_connections[from_node_name] = {"main": []}
            for branch in conn_data.get("main", []):
                new_targets = []
                for conn in branch:
                    target = conn.get("node", "")
                    # รองรับทั้ง id และ name
                    if target in node_ids:
                        target_name = id_map[target]
                    else:
                        target_name = target.strip().lower()
                    if target_name not in node_names_lower:
                        self.log_message(f"⚠️ Skipping connection to unknown node: {conn.get('node')}")
                        continue
                    new_targets.append({
                        "node": target_name,
                        "type": conn.get("type", "main"),
                        "index": conn.get("index", 0)
                    })
                if new_targets:
                    fixed_connections[from_node_name]["main"].append(new_targets)

        # --- Auto-connect linearly if no valid connections ---
        if not fixed_connections and len(fixed_nodes) > 1:
            self.log_message("⚠️ No connections found — auto-connecting nodes linearly.")
            for i in range(len(fixed_nodes) - 1):
                from_node_name = fixed_nodes[i]["name"].strip().lower()
                to_node_name = fixed_nodes[i + 1]["name"].strip().lower()
                fixed_connections.setdefault(from_node_name, {"main": [[]]})
                fixed_connections[from_node_name]["main"][0].append({
                    "node": to_node_name,
                    "type": "main",
                    "index": 0
                })

        # --- Final structure ---
        workflow["nodes"] = fixed_nodes
        workflow["connections"] = fixed_connections
        workflow.setdefault("name", "Auto Generated Workflow")
        workflow.setdefault("active", False)
        workflow.setdefault("settings", {})
        workflow.setdefault("version", 1)
        workflow.setdefault("meta", {})

        self.log_message(f"✅ Workflow validated and fixed: {len(fixed_nodes)} nodes, {len(fixed_connections)} connections.")
        return workflow

    def fix_connections(self, workflow):
        self.log.debug("🔧 Fixing connections...")
        nodes = workflow.get("nodes", [])
        node_names = [node.get("name") for node in nodes]
        connections = workflow.get("connections", {})

        fixed_connections = {}

        for from_node, conn_data in connections.items():
            if from_node not in node_names:
                self.log.warning(f"⚠️ Skipping connection from unknown node: {from_node}")
                continue

            fixed = {"main": []}
            if isinstance(conn_data, dict):
                for _, branch in conn_data.items():
                    parsed_branch = []
                    if isinstance(branch, list):
                        for entry in branch:
                            if isinstance(entry, dict) and "node" in entry and entry["node"] in node_names:
                                parsed_branch.append({
                                    "node": entry["node"],
                                    "type": entry.get("type", "main"),
                                    "index": entry.get("index", 0)
                                })
                            elif isinstance(entry, str) and entry in node_names:
                                parsed_branch.append({
                                    "node": entry,
                                    "type": "main",
                                    "index": 0
                                })
                    if parsed_branch:
                        fixed["main"].append(parsed_branch)

            elif isinstance(conn_data, list):
                branch = []
                for entry in conn_data:
                    if isinstance(entry, dict) and "node" in entry and entry["node"] in node_names:
                        branch.append(entry)
                    elif isinstance(entry, str) and entry in node_names:
                        branch.append({
                            "node": entry,
                            "type": "main",
                            "index": 0
                        })
                if branch:
                    fixed["main"].append(branch)

            fixed_connections[from_node] = fixed
            self.log.debug(f"✅ Fixed connection for '{from_node}': {fixed}")

        if not fixed_connections and len(nodes) >= 2:
            self.log.warning("⚠️ No connections found — auto-connecting nodes linearly.")
            for i in range(len(nodes) - 1):
                fixed_connections.setdefault(nodes[i]["name"], {"main": [[]]})
                fixed_connections[nodes[i]["name"]]["main"][0].append({
                    "node": nodes[i + 1]["name"],
                    "type": "main",
                    "index": 0
                })

        workflow["connections"] = fixed_connections
        return workflow


    def extract_json_from_response(self, text):
        import json
        import re
        

        VALID_NODE_TYPES = {
            "webhook", "manualTrigger", "code", "set", "if", "httpRequest", "openAi", "notion", "googleSheets"
        }

        if isinstance(text, dict):
            return self.validate_and_fix_workflow(text)

        if not isinstance(text, str):
            self.log.error("❌ Unexpected response type: %s", type(text))
            return self.fallback_workflow()

        # ลบ Markdown code block (```json ... ```)
        text = text.replace("```json", "").replace("```", "").strip()

        # พยายาม extract JSON หลายวิธี
        json_patterns = [
            r"```json\s*(\{[\s\S]*?\})\s*```",
            r"```\s*(\{[\s\S]*?\})\s*```",
            r"(\{[\s\S]*\})",
        ]
        for i, pattern in enumerate(json_patterns):
            matches = re.findall(pattern, text, re.DOTALL | re.MULTILINE)
            for j, match in enumerate(matches):
                try:
                    json_str = match.strip()
                    parsed_json = json.loads(json_str)
                    self.log.info(f"✅ Successfully extracted JSON using method {i+1}, match {j+1}")
                    return self.validate_and_fix_workflow(parsed_json)
                except Exception as e:
                    self.log.warning(f"❌ JSON parse failed for method {i+1}, match {j+1}: {str(e)}")
                    continue

        # Manual brace matching fallback
        try:
            brace_count = 0
            start_pos = text.find('{')
            if start_pos != -1:
                for i, char in enumerate(text[start_pos:], start_pos):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_str = text[start_pos:i+1]
                            parsed_json = json.loads(json_str)
                            self.log.info("✅ Successfully extracted JSON using manual brace matching")
                            return self.validate_and_fix_workflow(parsed_json)
        except Exception as e:
            self.log.warning(f"❌ Manual brace matching failed: {str(e)}")

        self.log.warning("❌ All JSON extraction methods failed")
        return self.fallback_workflow()
        
    def image_to_base64(self, image_path):
        with open(image_path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("utf-8")
        return encoded

    def fix_custom_connections_format(self, workflow):
        if not isinstance(workflow, dict):
            return workflow

        valid_connections = {}

        for node in workflow.get("nodes", []):
            name = node.get("name")
            raw_conns = node.get("connections", {})
            if not raw_conns:
                continue

            main_conns = []
            for target_key in raw_conns:
                targets = raw_conns[target_key]
                if isinstance(targets, list):
                    for idx, target_node_name in enumerate(targets):
                        main_conns.append([{
                            "node": target_node_name,
                            "type": "main",
                            "index": 0
                        }])

            if main_conns:
                valid_connections[name] = {
                    "main": main_conns
                }

        workflow["connections"] = valid_connections
        return workflow


    def auto_connect_linear_nodes(self, workflow):
        nodes = workflow.get("nodes", [])
        connections = {}

        for i in range(len(nodes) - 1):
            from_node = str(nodes[i]["id"])
            to_node = str(nodes[i + 1]["id"])

            connections.setdefault(from_node, {"main": [[]]})
            connections[from_node]["main"][0].append({
                "node": to_node,
                "type": "main",
                "index": 0
            })

        workflow["connections"] = connections
        return workflow

    def fix_connections(self, workflow):
        self.log.debug("🔧 Fixing connections...")
        nodes = workflow.get("nodes", [])
        name_map = {node["id"]: node["name"] for node in nodes if "id" in node and "name" in node}
        connections = workflow.get("connections", {})

        fixed_connections = {}

        for from_name, conns in connections.items():
            self.log.debug(f"🔍 Checking connection for node '{from_name}': {conns}")
            fixed = {"main": []}
            if isinstance(conns, dict):
                for branch_key, branch_val in conns.items():
                    if isinstance(branch_val, list):
                        # flatten list of lists or dicts
                        for entry in branch_val:
                            branch = []
                            if isinstance(entry, dict) and "node" in entry:
                                branch.append(entry)
                            elif isinstance(entry, str):
                                branch.append({
                                    "node": entry,
                                    "type": "main",
                                    "index": 0
                                })
                            elif isinstance(entry, list):
                                for sub in entry:
                                    if isinstance(sub, dict):
                                        branch.append(sub)
                                    elif isinstance(sub, str):
                                        branch.append({
                                            "node": sub,
                                            "type": "main",
                                            "index": 0
                                        })
                            if branch:
                                fixed["main"].append(branch)
            elif isinstance(conns, list):
                for entry in conns:
                    if isinstance(entry, dict):
                        fixed["main"].append([entry])
                    elif isinstance(entry, str):
                        fixed["main"].append([{
                            "node": entry,
                            "type": "main",
                            "index": 0
                        }])
            fixed_connections[from_name] = fixed
            self.log.debug(f"✅ Fixed connection for '{from_name}': {fixed}")

        # ❗ Auto-generate linear chain if no valid connections
        if not fixed_connections and len(nodes) > 1:
            self.log.warning("⚠️ No connections found — auto-generating linear chain.")
            for i in range(len(nodes) - 1):
                from_node = nodes[i]["name"]
                to_node = nodes[i + 1]["name"]
                fixed_connections.setdefault(from_node, {"main": [[]]})
                fixed_connections[from_node]["main"][0].append({
                    "node": to_node,
                    "type": "main",
                    "index": 0
                })

        workflow["connections"] = fixed_connections
        return workflow




    def build_deepseek_prompt(self, refined_logic: str, template_json: dict) -> str:
        """
        สร้าง prompt เพื่อให้ DeepSeek-Coder เจน workflow ที่ valid ตามโครงสร้าง n8n โดยใช้ template เป็นแม่แบบ schema
        """
        return f"""
    You are a developer assistant that converts workflow descriptions into valid n8n-compatible JSON.

    Below is a reference JSON template. Use only its structure (keys, formatting), not its actual values or content.

    ```json
    {json.dumps(template_json, indent=2)}```"""


    def validate_generated_workflow(self, wf):
        if not isinstance(wf, dict):
            return False
        return "nodes" in wf and "connections" in wf and isinstance(wf["nodes"], list)

class N8nApiClient:
    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or os.getenv("N8N_BASE_URL", "http://localhost:5678")).rstrip("/")
        self.api_key = api_key or os.getenv("N8N_API_KEY")
        self.headers = {
            "Content-Type": "application/json",
            "X-N8N-API-KEY": self.api_key
        }

    def create_workflow(self, workflow_data):
        url = f"{self.base_url}/api/v1/workflows"
        res = requests.post(url, headers=self.headers, json=workflow_data)
        if not res.ok:
            raise Exception(f"❌ Failed to create workflow: {res.status_code} {res.text}")
        return res.json()

    def activate_workflow(self, workflow_id):
        url = f"{self.base_url}/api/v1/workflows/{workflow_id}/activate"
        headers = self.headers.copy()
        headers.pop("Content-Type", None)
        res = requests.post(url, headers=headers)
        if not res.ok:
            raise Exception(f"❌ Failed to activate workflow: {res.status_code} {res.text}")
        return res.json()


class DiagramToN8nApp:
    def fix_workflow_for_n8n(self, workflow):
        import uuid

        # 1. ลบ property ที่ n8n ไม่รองรับ (เหลือแค่ name, nodes, connections, settings, active, version)
        allowed_keys = {"name", "nodes", "connections", "settings"}
        for key in list(workflow.keys()):
            if key not in allowed_keys:
                workflow.pop(key, None)
        workflow.setdefault("settings", {})



        # 2. ลบ node ที่ type เป็น noOp หรือชื่อ End/Output/End of Process (ไม่ให้สร้าง noOp node)
        forbidden_names = {"end", "output/end", "end of process"}
        workflow["nodes"] = [
            node for node in workflow.get("nodes", [])
            if node.get("type") != "n8n-nodes-base.noOp"
            and node.get("name", "").strip().lower() not in forbidden_names
        ]

        # 3. connections: ลบ connections ที่โยงไปหา node ที่ถูกลบ
        nodes = workflow.get("nodes", [])
        valid_names = set(node["name"] for node in nodes)
        name_map = {node["name"].strip().lower(): node["name"] for node in nodes}
        new_connections = {}

        # 4. แก้ IF node ให้มีแค่ 2 ทาง (true/false) เท่านั้น
        for node in nodes:
            if node["type"] == "n8n-nodes-base.if":
                node_name = node["name"]
                main_branches = []
                if node_name in workflow.get("connections", {}):
                    v = workflow["connections"][node_name]
                    for idx, branch in enumerate(v.get("main", [])):
                        if idx > 1:
                            break  # เอาแค่ 2 branch
                        branch_connections = []
                        for conn in branch:
                            target_name = name_map.get(str(conn["node"]).strip().lower())
                            if target_name and target_name in valid_names:
                                branch_connections.append({
                                    "node": target_name,
                                    "type": "main",
                                    "index": idx
                                })
                        if branch_connections:
                            main_branches.append(branch_connections)
                if len(main_branches) == 1:
                    main_branches.append([])
                if len(main_branches) > 2:
                    main_branches = main_branches[:2]
                if main_branches:
                    new_connections[node_name] = {"main": main_branches}

        # 5. เชื่อมต่อ node อื่นๆ แบบ linear ถ้าไม่มี connections
        if not new_connections:
            for i in range(len(nodes) - 1):
                from_name = nodes[i]["name"]
                to_name = nodes[i + 1]["name"]
                new_connections.setdefault(from_name, {"main": [[]]})
                new_connections[from_name]["main"][0].append({
                    "node": to_name,
                    "type": "main",
                    "index": 0
                })

        # 6. ลบ connections ที่โยงไปหา node ที่ถูกลบ (noOp หรือ End)
        for from_name in list(new_connections.keys()):
            main_branches = new_connections[from_name].get("main", [])
            filtered_branches = []
            for branch in main_branches:
                filtered_branch = [
                    conn for conn in branch
                    if conn.get("node") in valid_names
                ]
                if filtered_branch:
                    filtered_branches.append(filtered_branch)
            if filtered_branches:
                new_connections[from_name]["main"] = filtered_branches
            else:
                del new_connections[from_name]

        workflow["connections"] = new_connections
        workflow.setdefault("name", "Auto Generated Workflow")
        workflow.setdefault("version", 1)
        workflow.setdefault("settings", {})
        workflow.setdefault("active", False)

        # ✅ เพิ่ม AI Agent node ถ้าจำเป็น (วางตรงนี้)
        workflow = self.add_ai_agent_node_if_needed(workflow)

        return workflow
    def get_valid_node_types(self):
        """
        Return a set of all valid node types from the node knowledge base.
        """
        if hasattr(self, "node_knowledge_base"):
            return set(node["type"] for node in self.node_knowledge_base)
        # fallback
        return {
            "n8n-nodes-base.manualTrigger", "n8n-nodes-base.webhook", "n8n-nodes-base.cron",
            "n8n-nodes-base.set", "n8n-nodes-base.code", "n8n-nodes-base.if", "n8n-nodes-base.switch",
            "n8n-nodes-base.merge", "n8n-nodes-base.wait", "n8n-nodes-base.noOp", "n8n-nodes-base.httpRequest",
            "n8n-nodes-base.emailSend", "n8n-nodes-base.googleSheets", "n8n-nodes-base.notion",
            "n8n-nodes-base.airtable", "n8n-nodes-base.mySql", "n8n-nodes-base.postgres",
            "n8n-nodes-base.slack", "n8n-nodes-base.line", "n8n-nodes-base.telegram", "n8n-nodes-base.discord",
            "n8n-nodes-base.facebookMessenger", "n8n-nodes-base.dropbox", "n8n-nodes-base.googleDrive",
            "@n8n/n8n-nodes-langchain.openAi", "@n8n/n8n-nodes-langchain.anthropic",
            "@n8n/n8n-nodes-langchain.chatTrigger", "@n8n/n8n-nodes-langchain.textClassifier",
            "@n8n/n8n-nodes-langchain.informationExtractor", "@n8n/n8n-nodes-langchain.agent",
            "@n8n/n8n-nodes-langchain.lmChatOllama"
        }
    
    
    def on_generate_workflow_click(self):
        if not self.uploaded_image_path:
            self.log_message("⚠️ Please upload an image first.")
            return

        user_description = self.diagram_description_entry.get("1.0", "end").strip()
        self.log_message("📤 Generating workflow from image...")

        # 1. OCR วิเคราะห์ภาพแบบ app1.py
        flowchart_text = self.ocr_flowchart_to_text(self.uploaded_image_path)
        if not flowchart_text or "No text could be extracted" in flowchart_text:
            messagebox.showerror("Error", "ไม่สามารถวิเคราะห์ flowchart ได้ กรุณาลองใหม่")
            return

        # 2. Clean/process ข้อความ
        cleaned_text = self.clean_flowchart_text(flowchart_text)
        flow_elements = self.extract_flow_elements(cleaned_text)
        trigger_node_type = self.detect_smart_trigger_node(cleaned_text)
        prompt_type = self.select_prompt_template(cleaned_text)
        prompt = self.build_prompt_using_template(
            prompt_type, cleaned_text, flow_elements, trigger_node_type, user_description, getattr(self, 'current_analysis_id', f"flow_{datetime.now().strftime('%H%M%S')}")
        )

        # 3. Generate workflow ด้วย DeepSeek (หรือโมเดลที่เลือก)
        payload = {
            "model": self.selected_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 4096,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "seed": hash(getattr(self, 'current_analysis_id', '')) % 1000000
            }
        }
        try:
            response = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=600)
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

            data = response.json()
            workflow_json = data.get('response', '')
            self.generated_workflow = workflow_json
            self.log_message(f"✅ Generated workflow for ID: {getattr(self, 'current_analysis_id', '')}")
            self.show_raw_response_popup(workflow_json, title=f"🔄 N8N Workflow - {getattr(self, 'current_analysis_id', '')[:8]}")
            workflow_data = self.extract_json_from_response(workflow_json)
            if workflow_data is None:
                self.log_message("⚠️ Creating fallback n8n workflow")
                workflow_data = self.create_fallback_n8n_workflow(flow_elements, getattr(self, 'current_analysis_id', ''))

            # ใช้ validate_and_fix_workflow แบบเดียวกับ app1.py
            workflow_data = self.validate_and_fix_workflow(workflow_data)
            self.generated_workflow = workflow_data
            self.show_workflow_json()
            self.log_message(f"✅ Workflow generated with {len(workflow_data['nodes'])} nodes.")
            self.upload_button.configure(state="normal")
            self.download_button.configure(state="normal")
            self.notebook.set("📄 Output")
        except Exception as e:
            self.log_message(f"❌ Workflow generation failed: {str(e)}")
            messagebox.showerror("Error", f"Workflow generation failed: {str(e)}")

    def extract_json_from_response(self, text):
        import json
        import re

        if isinstance(text, dict):
            return text

        if not isinstance(text, str):
            self.log_message("❌ Unexpected response type for JSON extraction.")
            return None

        # Remove Markdown code block
        text = text.replace("```json", "").replace("```", "").strip()

        json_patterns = [
            r"```json\s*(\{[\s\S]*?\})\s*```",
            r"```\s*(\{[\s\S]*?\})\s*```",
            r"(\{[\s\S]*\})",
        ]
        for i, pattern in enumerate(json_patterns):
            matches = re.findall(pattern, text, re.DOTALL | re.MULTILINE)
            for j, match in enumerate(matches):
                try:
                    json_str = match.strip()
                    parsed_json = json.loads(json_str)
                    self.log_message(f"✅ Successfully extracted JSON using method {i+1}, match {j+1}")
                    return parsed_json
                except Exception as e:
                    self.log_message(f"❌ JSON parse failed for method {i+1}, match {j+1}: {str(e)}")
                    continue

        # Manual brace matching fallback
        try:
            brace_count = 0
            start_pos = text.find('{')
            if start_pos != -1:
                for i, char in enumerate(text[start_pos:], start_pos):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_str = text[start_pos:i+1]
                            parsed_json = json.loads(json_str)
                            self.log_message("✅ Successfully extracted JSON using manual brace matching")
                            return parsed_json
        except Exception as e:
            self.log_message(f"❌ Manual brace matching failed: {str(e)}")

        self.log_message("❌ All JSON extraction methods failed")
        return None


    def has_trigger_node(self, nodes):
        TRIGGER_NODE_TYPES = {
            "n8n-nodes-base.manualTrigger",
            "n8n-nodes-base.webhook",
            "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.cron",
            "@n8n/n8n-nodes-langchain.chatTrigger",
        }
        return any(node.get("type") in TRIGGER_NODE_TYPES for node in nodes)


    def log_message(self, message):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        full_message = f"{timestamp} - {message}\n"

        # ป้องกัน error ถ้ายังไม่มี log_text
        if hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", full_message)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        print(full_message)  # fallback log ไปที่ terminal

            
        # ทำให้เป็นเมธอดของคลาส
    def is_image_too_small(self, image_path, min_width=800, min_height=800):
        image = Image.open(image_path)
        return image.width < min_width or image.height < min_height

    def analyze_image_with_llava_or_ocr(self, image_path):
        if self.is_image_too_small(image_path):
            self.log_message("⚠️ ภาพมีขนาดเล็ก อาจวิเคราะห์ได้ไม่ครบ ลองใช้ภาพขนาด 1000x1000px ขึ้นไป")

        # 1. ลอง LLaVA ก่อน
        try:
            image_description = self.llava_model.generate(image_path)
            if "too small" in image_description.lower() or len(image_description.strip()) < 30:
                raise ValueError("Image unreadable by LLaVA")
            return image_description
        except Exception as e:
            self.log_message("🔁 วิเคราะห์ด้วย LLaVA ไม่สำเร็จ ใช้ OCR แทน")

        # 2. ลอง pytesseract (พร้อม preprocess)
        try:
            from PIL import ImageEnhance
            import pytesseract
            img = Image.open(image_path)
            # ขยายขนาด
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
            # grayscale
            img = img.convert('L')
            # เพิ่ม contrast
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(2.0)
            # threshold
            img = img.point(lambda p: p > 130 and 255)
            text = pytesseract.image_to_string(img, lang='eng')
            if text.strip() and len(text.strip()) > 10:
                self.log_message("✅ OCR ด้วย pytesseract สำเร็จ")
                return text
        except Exception as e:
            self.log_message(f"❌ pytesseract OCR failed: {str(e)}")

        # 3. ลอง easyocr (ถ้ามี)
        try:
            import easyocr
            reader = easyocr.Reader(['en'])
            result = reader.readtext(image_path, detail=0, paragraph=True)
            text = "\n".join(result)
            if text.strip() and len(text.strip()) > 10:
                self.log_message("✅ OCR ด้วย easyocr สำเร็จ")
                return text
        except Exception as e:
            self.log_message(f"❌ easyocr OCR failed: {str(e)}")

        self.log_message("❌ ไม่สามารถวิเคราะห์ภาพได้")
        return ""
        
    def upload_to_n8n(self):
        if not self.generated_workflow:
            self.log_message("⚠️ No workflow to upload.")
            return

        try:
            api_key = os.getenv("N8N_API_KEY")
            base_url = os.getenv("N8N_BASE_URL", "http://localhost:5678")
            headers = {
                "Content-Type": "application/json",
                "X-N8N-API-KEY": os.getenv("N8N_API_KEY")
            }

            # สร้าง workflow ใหม่
            response = requests.post(
                f"{base_url}/rest/workflows",
                headers=headers,
                json=self.generated_workflow
            )
            response.raise_for_status()
            workflow_id = response.json().get("id")

            # เปิดใช้งาน workflow
            if workflow_id:
                activate = requests.post(
                    f"{base_url}/rest/workflows/{workflow_id}/activate",
                    headers=headers
                )
                activate.raise_for_status()
                self.log_message(f"✅ Uploaded and activated workflow. ID: {workflow_id}")
            else:
                self.log_message("⚠️ Upload success but no workflow ID returned.")
        except Exception as e:
            self.log_message(f"❌ Failed to upload to n8n: {str(e)}")

    
    def show_workflow_json(self):
        if not hasattr(self, "generated_workflow") or self.generated_workflow is None:
            self.log_message("⚠️ No workflow available to show.")
            return

        workflow = self.generated_workflow

        try:
            self.json_output.delete("1.0", "end")
            self.json_output.insert("1.0", json.dumps(workflow, indent=2))
            self.log_message(f"✅ Showing workflow with {len(workflow.get('nodes', []))} nodes.")
        except Exception as e:
            self.log_message(f"❌ Failed to show workflow: {str(e)}")

    
    def detect_smart_trigger_node(self, text):
        text = text.lower()

        if any(keyword in text for keyword in ["submit", "form", "user", "input", "send"]):
            return "n8n-nodes-base.webhook"
        elif any(keyword in text for keyword in ["cron", "every day", "schedule", "repeat", "timer", "hour"]):
            return "n8n-nodes-base.cron"
        elif any(keyword in text for keyword in ["chat", "message", "receive", "bot"]):
            return "@n8n/n8n-nodes-langchain.chatTrigger"
        elif "start" in text or "begin" in text:
            return "n8n-nodes-base.manualTrigger"
        else:
            return "n8n-nodes-base.manualTrigger"  # default fallback

    
    def get_image_hash(self, image: Image.Image):
        """คืนค่า hash ของรูปภาพ (ใช้สำหรับ unique id)"""
        try:
            # แปลงภาพเป็น bytes
            img_bytes = image.tobytes()
            # สร้าง hash
            return hashlib.sha256(img_bytes).hexdigest()
        except Exception as e:
            self.log_message(f"❌ Error hashing image: {str(e)}")
            return "nohash"

    def ocr_flowchart_to_text(self, image_path: str):
        """OCR ที่ปรับปรุงเฉพาะสำหรับ flowchart"""
        try:
            pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            image = Image.open(image_path)
            image_hash = self.get_image_hash(image)
            current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = f"{image_hash[:8]}_{current_time}"
            self.current_analysis_id = unique_id

            processed_versions = []
            # Version 1: ขาวดำ + ขยายขนาด + เพิ่มความคมชัด
            gray = image.convert('L')
            large_gray = gray.resize((gray.width * 4, gray.height * 4), Image.Resampling.LANCZOS)
            enhancer = ImageEnhance.Contrast(large_gray)
            sharp_gray = enhancer.enhance(2.5)
            binary = sharp_gray.point(lambda p: p > 130 and 255)
            processed_versions.append(("flowchart_optimized", binary))

            # Version 2: Original + contrast enhancement
            if image.mode != 'RGB':
                image = image.convert('RGB')
            enhancer = ImageEnhance.Contrast(image)
            contrast_img = enhancer.enhance(2.0)
            enhancer = ImageEnhance.Sharpness(contrast_img)
            sharp_img = enhancer.enhance(2.0)
            processed_versions.append(("color_enhanced", sharp_img))

            # Version 3: Aggressive preprocessing
            gray_aggressive = image.convert('L')
            huge_gray = gray_aggressive.resize((gray_aggressive.width * 6, gray_aggressive.height * 6), Image.Resampling.LANCZOS)
            binary_aggressive = huge_gray.point(lambda p: p > 120 and 255)
            processed_versions.append(("aggressive_binary", binary_aggressive))

            best_texts = []
            flowchart_configs = [
                r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 &(),-.',
                r'--oem 3 --psm 4 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 &(),-.',
                r'--oem 3 --psm 11',
                r'--oem 1 --psm 6'
            ]
            for version_name, processed_img in processed_versions:
                for config in flowchart_configs:
                    try:
                        text = pytesseract.image_to_string(processed_img, lang='eng', config=config)
                        if text.strip() and len(text.strip()) > 10:
                            quality_score = self.analyze_flowchart_text_quality(text)
                            best_texts.append((text, quality_score, version_name, config))
                            self.log_message(f"OCR attempt: {version_name} - Quality: {quality_score:.2f}")
                    except Exception:
                        continue

            # Google Vision OCR (optional)
            try:
                from google.cloud import vision
                import io
                client = vision.ImageAnnotatorClient()
                with io.open(image_path, 'rb') as image_file:
                    content = image_file.read()
                image_gcp = vision.Image(content=content)
                response = client.text_detection(image=image_gcp)
                texts = response.text_annotations
                if texts:
                    gcp_text = texts[0].description
                    score = self.analyze_flowchart_text_quality(gcp_text)
                    best_texts.append((gcp_text, score, "google_vision", ""))
            except Exception as e:
                self.log_message(f"❌ Google Vision failed: {str(e)}")

            if best_texts:
                best_texts.sort(key=lambda x: x[1], reverse=True)
                best_text = best_texts[0][0]
                self.log_message(f"✅ Best OCR result: {best_texts[0][2]} (Quality: {best_texts[0][1]:.2f})")
                return best_text
            else:
                self.log_message("⚠️ No good OCR results found")
                return "No text could be extracted from flowchart"
        except Exception as e:
            self.log_message(f"❌ Flowchart OCR failed: {str(e)}")
            return None

    def generate_valid_workflow_with_retry(self, logic_text, max_attempts=5):
        """
        วนซ้ำ generate workflow JSON จาก LLM จนกว่าจะ valid หรือครบ max_attempts
        """
        generator = N8nWorkflowGenerator(
            ollama_url=self.ollama_url,
            logger=self.log,
            message_callback=self.log_message
        )
        for attempt in range(1, max_attempts + 1):
            self.log_message(f"🧠 Attempt {attempt}: Generating workflow from LLM...")
            workflow_json = generator.generate_n8n_json_with_deepseek(logic_text)
            workflow = workflow_json  # ไม่ต้อง extract_json_from_response อีก
            if workflow and generator.validate_workflow_structure(workflow):
                self.log_message("✅ Valid workflow generated.")
                return workflow
            else:
                self.log_message(f"⚠️ Attempt {attempt} failed. Retrying...")

        # ถ้าทำไม่ได้จริงๆ
        self.log_message("❌ ไม่สามารถสร้าง workflow ที่สมบูรณ์ได้ กรุณาลองใหม่หรือเปลี่ยน prompt/model.")
        return None

    def analyze_flowchart_text_quality(self, text):
        """วิเคราะห์คุณภาพของข้อความที่ได้จาก OCR สำหรับ flowchart"""
        score = 0
        
        # ให้คะแนนตาม keywords ที่น่าจะมีใน flowchart
        flowchart_keywords = [
            'start', 'end', 'process', 'decision', 'input', 'output', 
            'if', 'then', 'else', 'loop', 'check', 'validate', 'generate',
            'upload', 'diagram', 'workflow', 'export', 'format', 'correct', 'wrong'
        ]
        
        text_lower = text.lower()
        for keyword in flowchart_keywords:
            if keyword in text_lower:
                score += 1
        
        # ลดคะแนนถ้ามีตัวอักษรแปลกๆ เยอะ
        weird_chars = len(re.findall(r'[^a-zA-Z0-9\s&(),.!?-]', text))
        score -= weird_chars * 0.1
        
        # เพิ่มคะแนนถ้ามีคำที่สมบูรณ์
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text)
        score += len(words) * 0.1
        
        return max(0, score)

    def _get_n8n_node_knowledge_base(self):
        # เพิ่ม node หลักๆ ของ n8n ให้ครบถ้วน
        return [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "keywords": ["webhook", "http", "callback", "api", "receive"], "description": "Trigger via HTTP"},
            {"name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "keywords": ["manual", "start", "begin", "test"], "description": "Manual start"},
            {"name": "Cron", "type": "n8n-nodes-base.cron", "keywords": ["cron", "schedule", "timer", "interval", "every"], "description": "Scheduled trigger"},
            {"name": "IF", "type": "n8n-nodes-base.if", "keywords": ["if", "condition", "branch", "decision", "yes", "no"], "description": "Conditional branch"},
            {"name": "Switch", "type": "n8n-nodes-base.switch", "keywords": ["switch", "case", "route", "option", "multi"], "description": "Multi-branch"},
            {"name": "Set", "type": "n8n-nodes-base.set", "keywords": ["set", "assign", "value", "data", "field"], "description": "Set data"},
            {"name": "Code", "type": "n8n-nodes-base.code", "keywords": ["code", "js", "javascript", "custom", "logic"], "description": "Custom JS"},
            {"name": "HTTP Request", "type": "n8n-nodes-base.httpRequest", "keywords": ["http", "request", "api", "call", "fetch", "post", "get"], "description": "HTTP/API call"},
            {"name": "OpenAI", "type": "@n8n/n8n-nodes-langchain.openAi", "keywords": ["ai", "openai", "gpt", "chatgpt", "llm"], "description": "OpenAI LLM"},
            {"name": "Notion", "type": "n8n-nodes-base.notion", "keywords": ["notion", "note", "database", "page"], "description": "Notion integration"},
            {"name": "Google Sheets", "type": "n8n-nodes-base.googleSheets", "keywords": ["sheet", "google", "spreadsheet", "excel"], "description": "Google Sheets"},
            {"name": "SplitInBatches", "type": "n8n-nodes-base.splitInBatches", "keywords": ["split", "batch", "chunk", "divide"], "description": "Batch processing"},
            {"name": "Merge", "type": "n8n-nodes-base.merge", "keywords": ["merge", "combine", "join", "union"], "description": "Merge data"},
            {"name": "Wait", "type": "n8n-nodes-base.wait", "keywords": ["wait", "delay", "pause", "sleep"], "description": "Wait step"},
            {"name": "NoOp", "type": "n8n-nodes-base.noOp", "keywords": ["end", "stop", "noop", "finish"], "description": "End/No operation"},
            # เพิ่ม node อื่นๆ เช่น email, slack, discord, telegram, airtable, mysql, postgres, etc.
            {"name": "LINE Messaging", "type": "n8n-nodes-base.line", "keywords": ["line", "messaging", "chat"], "description": "LINE Messaging API"},
            {"name": "WhatsApp", "type": "n8n-nodes-base.whatsapp", "keywords": ["whatsapp", "chat", "message"], "description": "WhatsApp integration"},
            {"name": "Telegram", "type": "n8n-nodes-base.telegram", "keywords": ["telegram", "chat", "message"], "description": "Telegram integration"},
            {"name": "Discord", "type": "n8n-nodes-base.discord", "keywords": ["discord", "chat", "message"], "description": "Discord integration"},
            {"name": "Facebook Messenger", "type": "n8n-nodes-base.facebookMessenger", "keywords": ["facebook", "messenger", "chat"], "description": "Facebook Messenger"},
            {"name": "Google Drive", "type": "n8n-nodes-base.googleDrive", "keywords": ["google drive", "file", "storage"], "description": "Google Drive"},
            {"name": "Dropbox", "type": "n8n-nodes-base.dropbox", "keywords": ["dropbox", "file", "storage"], "description": "Dropbox"},
            {"name": "Twilio", "type": "n8n-nodes-base.twilio", "keywords": ["twilio", "sms", "phone", "call"], "description": "Twilio SMS/Call"},
            {"name": "Email", "type": "n8n-nodes-base.emailSend", "keywords": ["email", "mail", "send", "smtp"], "description": "Send email"},
            {"name": "Slack", "type": "n8n-nodes-base.slack", "keywords": ["slack", "message", "chat"], "description": "Slack integration"},
            {"name": "Discord", "type": "n8n-nodes-base.discord", "keywords": ["discord", "message", "chat"], "description": "Discord integration"},
            {"name": "Telegram", "type": "n8n-nodes-base.telegram", "keywords": ["telegram", "message", "chat"], "description": "Telegram integration"},
            {"name": "Airtable", "type": "n8n-nodes-base.airtable", "keywords": ["airtable", "table", "database"], "description": "Airtable integration"},
            {"name": "MySQL", "type": "n8n-nodes-base.mySql", "keywords": ["mysql", "database", "sql", "query"], "description": "MySQL"},
            {"name": "Postgres", "type": "n8n-nodes-base.postgres", "keywords": ["postgres", "database", "sql", "query"], "description": "PostgreSQL"},
        ]

    def suggest_node_type(self, step_text):
        step_lower = step_text.lower()
        # mapping พิเศษ
        if "upload" in step_lower or "submit" in step_lower:
            return "n8n-nodes-base.webhook"
        if "export" in step_lower or "send" in step_lower or "email" in step_lower or "notify" in step_lower:
            return "n8n-nodes-base.emailSend"
        if "check" in step_lower or "validate" in step_lower or "if" in step_lower or "decision" in step_lower:
            return "n8n-nodes-base.if"
        if "analyze" in step_lower or "summarize" in step_lower or "ai" in step_lower:
            return "@n8n/n8n-nodes-langchain.openAi"
        # mapping จาก knowledge base
        best_match = None
        max_matches = 0
        for node in self.node_knowledge_base:
            match_count = sum(1 for kw in node["keywords"] if kw in step_lower)
            if match_count > max_matches:
                best_match = node
                max_matches = match_count
        if best_match:
            return best_match["type"]
        else:
            return "n8n-nodes-base.code"

    def add_ai_agent_node_if_needed(self, workflow):
        import uuid
        ai_keywords = ["ai", "agent", "intelligent", "analyze", "predict", "recommend", "learn", "understand", "interpret"]
        found_ai = False
        for node in workflow.get("nodes", []):
            if node["type"] == "@n8n/n8n-nodes-langchain.agent":
                found_ai = True
                break
            if any(kw in node.get("name", "").lower() for kw in ai_keywords):
                found_ai = True
                break

        if not found_ai:
            ai_node = {
                "id": str(uuid.uuid4()),
                "name": "AI Agent",
                "type": "@n8n/n8n-nodes-langchain.agent",
                "typeVersion": 1,
                "position": [100, 600],
                "parameters": {
                    # ให้ user ไปเลือกเองใน n8n UI
                }
            }
            workflow["nodes"].append(ai_node)
            # เชื่อมต่อ node สุดท้ายไปหา AI Agent (optional)
            if len(workflow["nodes"]) > 1:
                last_node = workflow["nodes"][-2]["name"]
                workflow["connections"].setdefault(last_node, {"main": [[]]})
                workflow["connections"][last_node]["main"][0].append({
                    "node": ai_node["name"],
                    "type": "main",
                    "index": 0
                })
        return workflow
    def flowchart_to_n8n_workflow(self):
        unique_id = getattr(self, 'current_analysis_id', f"flow_{datetime.now().strftime('%H%M%S')}")
        self.log_message(f"🔄 เริ่มการวิเคราะห์ Flowchart ID: {unique_id}")

        # 1️⃣ วิเคราะห์ภาพด้วย LLaVA
        self.log_message("📸 วิเคราะห์ภาพด้วย LLaVA...")
        step1_text = self.analyze_image_with_llava(self.uploaded_image_path)

        if "name" not in workflow_json or not workflow_json["name"]:
            workflow_json["name"] = "Auto Generated Workflow"

        if hasattr(self, "llava_output_box") and step1_text:
            self.llava_output_box.delete("1.0", "end")
            self.llava_output_box.insert("1.0", step1_text)

        # 2️⃣ วิเคราะห์ logic ด้วย Phi-3
        self.log_message("🧠 วิเคราะห์ logic ด้วย Phi-3...")
        logic_text = self.refine_logic_with_phi3(step1_text)

        # 3️⃣ สร้าง JSON ด้วย DeepSeek-Coder
        self.log_message("⚙️ แปลง logic เป็น n8n JSON ด้วย DeepSeek...")
        prompt = (
            f"Convert the following flowchart logic into a valid n8n workflow JSON. "
            f"Include proper node types, connections, and structure:\n\n{logic_text}"
        )

        payload = {
            "model": "deepseek-coder:6.7b",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 4096,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "seed": hash(unique_id) % 1000000
            }
        }

        try:
            response = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=600)
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

            data = response.json()
            workflow_json = data.get('response', '')
            self.log_message(f"✅ สร้าง workflow สำเร็จ (ID: {unique_id})")
            self.show_raw_response_popup(workflow_json, title=f"🔄 N8N Workflow - {unique_id[:8]}")

            # ตัด JSON ออกมา
            workflow_data = self.extract_json_from_response(workflow_json)
            if workflow_data is None:
                self.log_message("⚠️ แปลง JSON ไม่สมบูรณ์ — สร้าง fallback workflow")
                workflow_data = self.create_fallback_n8n_workflow([], unique_id)

            workflow_data = self.validate_and_fix_workflow(workflow_data, unique_id)
            return workflow_data

        except Exception as e:
            self.log_message(f"❌ สร้าง workflow ล้มเหลว: {str(e)}")
            return self.create_fallback_n8n_workflow([], unique_id)



    def clean_flowchart_text(self, text):
        """ทำความสะอาดข้อความจาก flowchart"""
        if not text:
            return "Empty flowchart"
        
        # ลบอักขระแปลกๆ
        cleaned = re.sub(r'[^\w\s&(),.!?-]', ' ', text)
        
        # ลบช่องว่างเกิน
        cleaned = re.sub(r'\s+', ' ', cleaned)
        
        # ลบบรรทัดว่าง
        lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        cleaned = '\n'.join(lines)
        
        return cleaned

    def select_prompt_template(self, diagram_text: str) -> str:
        selector_prompt = f"""
    You are an AI prompt selector. Given the diagram content below, identify which type of prompt template is the best fit.

    ### DIAGRAM TEXT:
    {diagram_text.strip()}

    ### TEMPLATE OPTIONS:
    1. flowchart-basic
    2. class-diagram
    3. ai-agent-orchestration
    4. multi-branch-decision
    5. loop-with-condition
    6. unclear-or-generic

    ### INSTRUCTION:
    Return ONLY the template name (e.g., 'flowchart-basic')
    """
        payload = {
            "model": "phi3:instruct",
            "prompt": selector_prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.95
            }
        }

        try:
            res = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=600)
            selected = res.json().get("response", "").strip()
            return selected if selected in [
                "flowchart-basic", "class-diagram", "ai-agent-orchestration",
                "multi-branch-decision", "loop-with-condition"
            ] else "unclear-or-generic"
        except:
            return "flowchart-basic"

    def build_prompt_using_template(self, template_name, cleaned_text, flow_elements, trigger_node_type, diagram_description, unique_id):
        prompt = f"""
    You are a professional n8n architect. Your job is to read a user's diagram and return a valid n8n workflow as JSON.
    - Each node must have a unique id (do not use "uuid-string", use "node1", "node2", ...).
    - Do not include any special tokens or artifacts.
    - Do not include markdown, comments, or explanations.
    - Output ONLY valid JSON. No explanations, no markdown, no code blocks.
    - Use only real node types from the list below.
    ### FLOWCHART TEXT
    {cleaned_text}

    ### USER DESCRIPTION
    {diagram_description if diagram_description else '[No description provided]'}

    ### SMART TRIGGER SUGGESTED
    Use: `{trigger_node_type}`

    ### START NODE
    {flow_elements.get('likely_start_nodes', [])}

    ### NODE SUGGESTIONS
    """
        for step in flow_elements.get("processes", []):
            suggested_node = self.suggest_node_type(step)
            prompt += f"- Step: '{step}' → Suggested Node: `{suggested_node}`\n"

        prompt += f"""

    ### ⛔ DO NOT:
    - Do not invent or hallucinate node types.
    - Only use node types listed here:
    ["webhook", "manualTrigger", "code", "set", "if", "httpRequest", "openAi", "notion", "googleSheets"]

    ### CONNECTION FORMAT EXAMPLE:
    "connections": {{
    "Webhook": {{
        "main": [[{{ "node": "Code", "type": "main", "index": 0 }}]]
    }},
    "Code": {{
        "main": [[{{ "node": "Set", "type": "main", "index": 0 }}]]
    }}
    }}

    ### FINAL OUTPUT FORMAT:
    {{
    "name": "Flowchart Process - {unique_id[:8]}",
    "nodes": [...],
    "connections": {{...}}
    }}

    ONLY return valid JSON. DO NOT include markdown or explanation.
    """
        return prompt

    def extract_flow_elements(self, text):
        """
        แยก process, decision, start/end จากข้อความ flowchart
        - OCR + user description
        - split ด้วย keyword ที่หลากหลาย
        - กรอง noise ด้วย regex
        - เดา start/end node ถ้าไม่มี
        """
        # รวมข้อความ OCR และ user description
        lines = []
        split_pattern = r"\n|\.|,|then|next|after|before|→|->|and|ต่อไป|ถัดไป|=>|;|→|->|→|->|and then|and next"
        if text:
            lines += [line.strip() for line in re.split(split_pattern, text, flags=re.IGNORECASE) if line.strip()]
        if hasattr(self, "diagram_description_entry"):
            desc = self.diagram_description_entry.get("1.0", "end").strip()
            if desc:
                lines += [line.strip() for line in re.split(split_pattern, desc, flags=re.IGNORECASE) if line.strip()]

        # กรอง noise: เอาเฉพาะข้อความที่มีความยาว > 2 และมีตัวอักษร a-z หรือกริยา
        lines = [line for line in lines if len(line) > 2 and re.search(r"[a-zA-Zก-ฮ]", line)]
        lines = list(dict.fromkeys(lines))  # unique, preserve order

        elements = {
            "processes": [],
            "decisions": [],
            "inputs_outputs": [],
            "start_end": [],
            "likely_start_nodes": []
        }

        # เพิ่ม keyword mapping ที่หลากหลาย
        process_keywords = ["upload", "analyze", "extract", "notify", "save", "report", "transform", "parse", "fetch", "calculate", "summarize", "classify", "export", "process", "generate", "create", "update", "delete"]
        decision_keywords = ["approve", "decision", "if", "else", "disapprove", "check", "validate", "format", "ถูกต้อง", "ผิด", "ตรวจสอบ", "choose", "select", "judge"]
        io_keywords = ["input", "output", "form", "export", "deployment", "json", "generate", "แสดงผล", "นำออก", "display", "print", "show"]

        for line in lines:
            low = line.lower()
            if any(kw in low for kw in decision_keywords):
                elements["decisions"].append(line)
            elif any(kw in low for kw in io_keywords):
                elements["inputs_outputs"].append(line)
            elif any(kw in low for kw in process_keywords):
                elements["processes"].append(line)
            else:
                # ถ้าไม่เข้าเงื่อนไขไหนเลย ให้ถือว่าเป็น process
                elements["processes"].append(line)

        # เดา start/end node ถ้าไม่มี
        if not elements["start_end"]:
            if elements["processes"]:
                elements["start_end"].append("Start")
                elements["likely_start_nodes"].append(elements["processes"][0])
            if elements["processes"]:
                elements["start_end"].append("End")

        self.log_message(f"DEBUG: flow_elements = {elements}")
        return elements


    def create_fallback_n8n_workflow(self, flow_elements, unique_id):
        """สร้าง n8n workflow พื้นฐานจาก flow elements"""
        nodes = []
        connections = {}
        # Start node
        start_node = {
            "id": f"{unique_id}-start",
            "name": "Start Process",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [100, 200],
            "parameters": {}
        }
        nodes.append(start_node)
        x_pos = 500
        previous_node = start_node["name"]
        for i, process in enumerate(flow_elements.get("processes", [])):
            node_id = f"{unique_id}-process-{i}"
            node_name = f"Code {i+1}: {process.title()}"
            process_node = {
                "id": node_id,
                "name": node_name,
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [x_pos, 200],
                "parameters": {
                    "language": "javascript",
                    "jsCode": f"// Process: {process}\nreturn items.map(item => {{\n  item.{process}_completed = true;\n  return item;\n}});"
                }
            }
            nodes.append(process_node)
            if previous_node not in connections:
                connections[previous_node] = {"main": [[]]}
            connections[previous_node]["main"][0].append({
                "node": node_name,
                "type": "main",
                "index": 0
            })
            previous_node = node_name
            x_pos += 400
        # Decision nodes
        for i, decision in enumerate(flow_elements.get("decisions", [])):
            node_id = f"{unique_id}-decision-{i}"
            node_name = f"Decision {i+1}: {decision[:40]}"
            decision_node = {
                "id": node_id,
                "name": node_name,
                "type": "n8n-nodes-base.if",
                "typeVersion": 2,
                "position": [x_pos, 200],
                "parameters": {
                    "conditions": {
                        "options": {
                            "caseSensitive": True,
                            "leftValue": f"{{{{ $json.{decision} }}}}",
                            "operation": "equal",
                            "rightValue": "correct"
                        }
                    }
                }
            }
            nodes.append(decision_node)
            if previous_node not in connections:
                connections[previous_node] = {"main": [[]]}
            connections[previous_node]["main"][0].append({
                "node": node_name,
                "type": "main",
                "index": 0
            })
            previous_node = node_name
            x_pos += 400
        return {
            "meta": {"instanceId": unique_id},
            "name": f"Flowchart Workflow - {unique_id[:8]}",
            "nodes": nodes,
            "connections": connections
        }
    

    def copy_json_output(self):
        json_text = self.json_output.get("1.0", "end-1c")
        self.log_message(f"DEBUG: Copy preview (first 300 chars): {json_text[:300]}")
        self.root.clipboard_clear()
        self.root.clipboard_append(json_text)
        self.log_message("✅ Copied JSON to clipboard.")

    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title("Class Diagram to n8n Workflow Generator")
        self.root.geometry("1400x900")
        self.log = logging.getLogger(__name__)
        self.root.after(100, self.maximize_window)

        # Application state
        self.uploaded_image_path = None
        self.uploaded_image = None
        self.selected_model = "deepseek-coder:6.7b"
        
        self.available_models = []
        self.generated_workflow = None
        self.selected_model = self.selected_model or "deepseek-coder:6.7b"
        model = getattr(self, 'selected_model', "deepseek-coder:6.7b")

        # Configuration
        self.ollama_url = "http://localhost:11434"
        self.n8n_url = "http://localhost:5678"
        self.n8n_api_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI4MDBiMmQ5Ny1lYWE2LTQyMWEtYmMyZC1iMDY1OTJlM2IyMDMiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzQ5NzA5MDUwLCJleHAiOjE3NTQ4ODQ4MDB9.crdXtiPbyO38OeS0984C_MvbtoAh7KsXVdoqYrFWchM"
        self.setup_ui()
        self.upload_button.configure(state="normal")
        self.root.after(100, self.check_ollama_connection)
        self.node_knowledge_base = [
    {
        "name": "Webhook",
        "type": "n8n-nodes-base.webhook",
        "keywords": ["submit", "form", "user input", "http", "receive"],
        "description": "Trigger workflow via external HTTP requests"
    },
    {
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "keywords": ["start", "manual", "begin"],
        "description": "Trigger workflow manually"
    },
    {
        "name": "Cron",
        "type": "n8n-nodes-base.cron",
        "keywords": ["cron", "schedule", "every", "interval", "timer", "repeat"],
        "description": "Trigger workflow on schedule"
    },
    {
        "name": "IF",
        "type": "n8n-nodes-base.if",
        "keywords": ["decision", "approve", "reject", "if", "disapprove", "check"],
        "description": "Branch workflow based on conditions"
    },
    {
        "name": "Switch",
        "type": "n8n-nodes-base.switch",
        "keywords": ["switch", "case", "route", "branch", "multi", "option"],
        "description": "Route data based on value"
    },
    {
        "name": "Set",
        "type": "n8n-nodes-base.set",
        "keywords": ["store", "record", "save", "tag", "status"],
        "description": "Set or modify data values"
    },
    {
        "name": "HTTP Request",
        "type": "n8n-nodes-base.httpRequest",
        "keywords": ["send", "notify", "post", "api", "call", "request"],
        "description": "Send requests to external APIs"
    },
    {
        "name": "Code",
        "type": "n8n-nodes-base.code",
        "keywords": ["process", "logic", "custom", "transform", "convert"],
        "description": "Write custom JavaScript to manipulate data"
    },
    {
        "name": "OpenAI",
        "type": "@n8n/n8n-nodes-langchain.openAi",
        "keywords": ["ai", "analyze", "classify", "summarize", "predict"],
        "description": "Use OpenAI models to process or generate text"
    },
    {
        "name": "Notion",
        "type": "n8n-nodes-base.notion",
        "keywords": ["notion", "note", "database", "page", "block", "workspace"],
        "description": "Interact with Notion workspace"
    },
    {
        "name": "Google Sheets",
        "type": "n8n-nodes-base.googleSheets",
        "keywords": ["sheet", "google", "spreadsheet", "excel", "row", "cell", "table"],
        "description": "Read/write Google Sheets"
    },
    {
        "name": "SplitInBatches",
        "type": "n8n-nodes-base.splitInBatches",
        "keywords": ["split", "batch", "chunk", "divide", "group"],
        "description": "Split data into batches"
    },
    {
        "name": "Merge",
        "type": "n8n-nodes-base.merge",
        "keywords": ["merge", "combine", "join", "union", "together"],
        "description": "Merge data from multiple sources"
    },
    {
        "name": "Wait",
        "type": "n8n-nodes-base.wait",
        "keywords": ["wait", "delay", "pause", "sleep", "hold"],
        "description": "Pause workflow for a period"
    },
    {
        "name": "NoOp",
        "type": "n8n-nodes-base.noOp",
        "keywords": ["end", "terminate", "stop"],
        "description": "Placeholder or end step"
    }
]

    def maximize_window(self):
        """เปิดหน้าต่างเต็มจอ"""
        self.root.state("zoomed")  # สำหรับ Windows


    def setup_ui(self):
        """Setup the main user interface"""
        self.notebook = ctk.CTkTabview(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=20)
        self.setup_upload_tab()
        self.setup_config_tab()
        self.tab_generate = self.notebook.add("⚡ Generate Workflow")  # <-- ย้ายขึ้นมา
        self.setup_generate_tab()
        self.setup_output_tab()
        
    # ...อื่นๆ...

    def setup_upload_tab(self):
        """Setup the image upload tab"""
        tab = self.notebook.add("📤 Upload Image")
        
        title_label = ctk.CTkLabel(tab, text="Upload Class Diagram", font=ctk.CTkFont(size=24, weight="bold"))
        title_label.pack(pady=20)
        
        upload_frame = ctk.CTkFrame(tab)
        upload_frame.pack(fill="both", expand=True, padx=20, pady=20)


        formats_label = ctk.CTkLabel(upload_frame,
                                     text="Supported formats: PNG, JPG, JPEG, WebP",
                                     font=ctk.CTkFont(size=12))
        formats_label.pack(pady=10)
        self.upload_btn = ctk.CTkButton(
            upload_frame,
            text="📤 Upload Image",
            command=self.select_image_file
        )
        self.upload_btn.pack(pady=10)
        self.preview_frame = ctk.CTkFrame(upload_frame)
        self.preview_frame.pack(fill="both", expand=True, padx=20, pady=20)

        self.preview_label = ctk.CTkLabel(self.preview_frame, text="No image selected")
        self.preview_label.pack(expand=True)

        self.file_info_label = ctk.CTkLabel(upload_frame, text="")
        self.file_info_label.pack(pady=10)

    def select_image_file(self):
        """Handle image file selection"""
        file_types = [("Image files", "*.png *.jpg *.jpeg *.webp"),
                      ("PNG files", "*.png"),
                      ("JPEG files", "*.jpg *.jpeg"),
                      ("WebP files", "*.webp"),
                      ("All files", "*.*")]
        
        filename = filedialog.askopenfilename(
            title="Select Class Diagram Image",
            filetypes=file_types
        )

        if filename:
            self.process_image_file(filename)

    def process_image_file(self, filename):
        try:
            img = PILImage.open(filename)
            self.uploaded_image = img
            self.uploaded_image_path = filename
            # Fit-to-frame preview (no crop, show full image)
            preview_size = (400, 300)
            img_preview = ImageOps.contain(img.copy(), preview_size)
            img_tk = CTkImage(light_image=img_preview, size=img_preview.size)
            self.preview_label.configure(image=img_tk, text="")
            self.preview_label.image = img_tk  # สำคัญ! ป้องกัน image หาย
            # Show filename and original size
            self.file_info_label.configure(text=f"{os.path.basename(filename)} | {img.width}x{img.height} px (original)")
        except Exception as e:
            self.log_message(f"❌ Error loading image: {str(e)}")
            messagebox.showerror("Error", f"Failed to load image: {str(e)}")
            return
    # ...rest of function...
        # Reset state
        self.generated_workflow = None
        self.upload_button.configure(state="normal")
        self.json_output.delete("1.0", "end")
        self.download_button.configure(state="disabled")
        self.upload_button.configure(state="disabled")
        self.log_message(f"✅ Image loaded: {os.path.basename(filename)}")
        if hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")

    def plantuml_to_json_schema(self, plantuml_text):
        self.selected_model = self.model_dropdown.get()
        self.ollama_url = self.ollama_url_entry.get().strip()
        
        # Clean and preprocess the OCR text
        cleaned_text = self.clean_ocr_text(plantuml_text)
        diagram_description = self.diagram_description_entry.get("1.0", "end").strip()

        # Enhanced prompt with better examples and instructions
        prompt = f"""You are an expert system analyzer. Convert the following diagram text into a JSON schema.

IMPORTANT: Always output a valid JSON object, even if the input is unclear. If you cannot identify specific classes, create reasonable examples based on common software patterns.
Based on this flowchart text, generate a complete valid n8n workflow in JSON format.

Requirements:
- Only respond with the raw JSON (no explanations, no markdown, no extra text).
- Validate the JSON before returning.
Required JSON format:
{{
  "classes": [
    {{
      "name": "ClassName",
      "attributes": ["attribute1", "attribute2"],
      "methods": ["method1", "method2"],
      "responsibilities": "Brief description of what this class does"
    }}
  ],
  "relationships": [
    {{
      "from": "ClassA",
      "to": "ClassB",
      "type": "inheritance|composition|association|dependency",
      "description": "Description of relationship"
    }}
  ]
}}

Input diagram text:
    {cleaned_text}

USER DESCRIPTION:
    {diagram_description if diagram_description else '[No description provided]'}
If the text is unclear or incomplete, create a reasonable interpretation with at least 2-3 classes that might represent a typical software system (e.g., Controller, Service, Model classes).

Output only the JSON object, no explanations or markdown:"""

        if "No text could be extracted" in cleaned_text:
            self.log_message("⚠️ Diagram text was unclear. Forcing fallback schema creation.")
            return self.create_fallback_schema(cleaned_text)

        payload = {
        "model": self.selected_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1, 
            "num_predict": 2048,
            "top_p": 0.9,
            "repeat_penalty": 1.1
        }
    }

        try:
            response = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=600)
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

            data = response.json()
            analysis = data.get('response', '')

            self.log_message(f"Raw response length: {len(analysis)}")
            self.log_message(f"First 200 chars: {analysis[:200]}...")

            # Show raw response popup
            self.show_raw_response_popup(analysis, title="🧠 DeepSeek - JSON Schema Response")

            # Extract JSON from response
            analysis_json = self.extract_json_from_response(analysis)

            if analysis_json is None:
                # Fallback: Create a default schema if extraction fails
                self.log_message("⚠️ JSON extraction failed, creating fallback schema")
                analysis_json = self.create_fallback_schema(cleaned_text)

            # Validate the schema structure
            analysis_json = self.validate_and_fix_schema(analysis_json)

            self.log_message(f"✅ Successfully parsed JSON schema with {len(analysis_json.get('classes', []))} classes")
            return analysis_json

        except Exception as e:
            self.log_message(f"❌ DeepSeek schema failed: {str(e)}")
            # Create fallback schema instead of returning None
            self.log_message("⚠️ Creating fallback schema due to error")
            return self.create_fallback_schema(plantuml_text)

    def clean_ocr_text(self, text):
        """Clean and preprocess OCR text for better analysis"""
        if not text:
            return "No text extracted from image"
        
        # Remove excessive whitespace and clean up
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        cleaned = '\n'.join(lines)
        
        # Remove special characters that might confuse the model
        cleaned = re.sub(r'[^\w\s\-\+\(\)\[\]\{\}:\|]', ' ', cleaned)
        
        # Limit length to prevent token overflow
        if len(cleaned) > 1500:
            cleaned = cleaned[:1500] + "..."
        
        return cleaned

    def create_fallback_schema(self, text):
        """Create a fallback schema when OCR or LLM fails"""
        self.log_message("Creating fallback schema with common software patterns")
        
        # Try to extract any recognizable class names from text
        class_names = []
        if text:
            # Look for capitalized words that might be class names
            potential_classes = re.findall(r'\b[A-Z][a-zA-Z]+\b', text)
            class_names = list(set(potential_classes))[:3]  # Take up to 3 unique names
        
        # If no class names found, use defaults
        if not class_names:
            class_names = ["UserController", "UserService", "UserModel"]
        
        # Create fallback schema
        fallback_schema = {
            "classes": [],
            "relationships": []
        }
        
        for i, class_name in enumerate(class_names):
            class_info = {
                "name": class_name,
                "attributes": [f"id", f"name", f"status"],
                "methods": [f"get{class_name}", f"create{class_name}", f"update{class_name}"],
                "responsibilities": f"Handles {class_name.lower()} operations and data management"
            }
            fallback_schema["classes"].append(class_info)
            
            # Create relationships between classes
            if i > 0:
                fallback_schema["relationships"].append({
                    "from": class_names[i-1],
                    "to": class_name,
                    "type": "dependency",
                    "description": f"{class_names[i-1]} depends on {class_name}"
                })
        
        return fallback_schema

    def validate_and_fix_schema(self, schema):
        """Validate and fix the schema structure"""
        if not isinstance(schema, dict):
            return self.create_fallback_schema("")
        
        # Ensure required keys exist
        if "classes" not in schema:
            schema["classes"] = []
        if "relationships" not in schema:
            schema["relationships"] = []
        
        # Validate classes structure
        valid_classes = []
        for cls in schema.get("classes", []):
            if isinstance(cls, dict) and "name" in cls:
                # Fix missing fields
                if "attributes" not in cls:
                    cls["attributes"] = []
                if "methods" not in cls:
                    cls["methods"] = []
                if "responsibilities" not in cls:
                    cls["responsibilities"] = f"Handles {cls['name'].lower()} operations"
                
                # Ensure arrays are actually arrays
                if not isinstance(cls["attributes"], list):
                    cls["attributes"] = []
                if not isinstance(cls["methods"], list):
                    cls["methods"] = []
                
                valid_classes.append(cls)
        
        schema["classes"] = valid_classes
        
        # If no valid classes, create fallback
        if not valid_classes:
            return self.create_fallback_schema("")
        
        # Validate relationships
        valid_relationships = []
        class_names = [cls["name"] for cls in valid_classes]
        
        for rel in schema.get("relationships", []):
            if (isinstance(rel, dict) and 
                "from" in rel and "to" in rel and
                rel["from"] in class_names and rel["to"] in class_names):
                
                if "type" not in rel:
                    rel["type"] = "dependency"
                if "description" not in rel:
                    rel["description"] = f"{rel['from']} relates to {rel['to']}"
                
                valid_relationships.append(rel)
        
        schema["relationships"] = valid_relationships
        
        return schema
    
    def validate_and_fix(self, workflow):
        import uuid

        if not isinstance(workflow, dict):
            raise ValueError("Workflow is not a dictionary.")

        workflow.setdefault("name", "Generated Workflow")
        workflow.setdefault("nodes", [])
        workflow.setdefault("connections", {})
        workflow.setdefault("type", "n8n-workflow")
        workflow.setdefault("active", False)
        workflow.setdefault("version", 1)
        workflow.setdefault("schemaVersion", 1)

        required_fields = ["id", "name", "type", "position", "typeVersion", "parameters"]

        for node in workflow["nodes"]:
            for field in required_fields:
                if field not in node:
                    if field == "id":
                        node["id"] = str(uuid.uuid4())
                    elif field == "name":
                        node["name"] = node.get("type", "Unnamed Node")
                    elif field == "type":
                        node["type"] = "n8n-nodes-base.noOp"
                    elif field == "position":
                        node["position"] = [0, 0]
                    elif field == "typeVersion":
                        node["typeVersion"] = 1
                    elif field == "parameters":
                        node["parameters"] = {}

        # ✅ Reset invalid connections format
        if not isinstance(workflow["connections"], dict):
            self.log.warning("⚠️ Invalid connections format, resetting to empty.")
            workflow["connections"] = {}

        return workflow

    def validate_and_fix_workflow(self, workflow):
        """
        Validate and fix workflow structure, node types, and connections.
        ใช้ knowledge base เดียวกัน, robust mapping, และ normalize connections.
        """
        import uuid


        # --- Normalize node types and fields ---
        fixed_nodes = []
        name_map = {}
        id_map = {}

        for i, node in enumerate(workflow.get("nodes", [])):
            # Robust mapping
            node_type = self.robust_suggest_node_type(node.get("type"), node.get("name", ""))
            if node_type not in self.get_valid_node_types():
                self.log_message(f"⚠️ Skipping unsupported node type: {node.get('type')} ({node.get('name', node.get('id', 'no-name'))})")
                continue

            node["type"] = node_type
            node.setdefault("id", str(uuid.uuid4()))
            node.setdefault("name", node.get("name", node_type))
            node.setdefault("typeVersion", 1)
            node.setdefault("parameters", {})
            node.setdefault("position", [i * 400 + 100, 200])

            name_map[node["name"].strip().lower()] = node["id"]
            id_map[node["id"]] = node["name"].strip().lower()
            fixed_nodes.append(node)

        if not fixed_nodes:
            self.log_message("⚠️ No valid nodes found. Returning fallback workflow.")
            return self.create_fallback_n8n_workflow({}, "no_valid_nodes")

        # --- Normalize connections ---
        connections = workflow.get("connections", {})
        fixed_connections = {}

        node_names_lower = set(n["name"].strip().lower() for n in fixed_nodes)

        for from_node, conn_data in connections.items():
            from_node_norm = from_node.strip().lower()
            if from_node_norm not in node_names_lower:
                self.log_message(f"⚠️ Skipping connection from unknown node: {from_node}")
                continue

            fixed_connections[id_map.get(name_map.get(from_node_norm, ""), from_node)] = {"main": []}
            for branch in conn_data.get("main", []):
                new_targets = []
                for conn in branch:
                    target_name = conn.get("node", "").strip().lower()
                    if target_name not in node_names_lower:
                        self.log_message(f"⚠️ Skipping connection to unknown node: {conn.get('node')}")
                        continue
                    new_targets.append({
                        "node": id_map.get(name_map.get(target_name, ""), conn.get("node")),
                        "type": conn.get("type", "main"),
                        "index": conn.get("index", 0)
                    })
                if new_targets:
                    fixed_connections[id_map.get(name_map.get(from_node_norm, ""), from_node)]["main"].append(new_targets)

        # --- Auto-connect linearly if no valid connections ---
        if not fixed_connections and len(fixed_nodes) > 1:
            self.log_message("⚠️ No connections found — auto-connecting nodes linearly.")
            for i in range(len(fixed_nodes) - 1):
                fixed_connections.setdefault(fixed_nodes[i]["id"], {"main": [[]]})
                fixed_connections[fixed_nodes[i]["id"]]["main"][0].append({
                    "node": fixed_nodes[i + 1]["id"],
                    "type": "main",
                    "index": 0
                })

        # --- Final structure ---
        workflow["nodes"] = fixed_nodes
        workflow["connections"] = fixed_connections
        workflow.setdefault("name", "Auto Generated Workflow")
        workflow.setdefault("active", False)
        workflow.setdefault("settings", {})
        workflow.setdefault("version", 1)
        workflow.setdefault("meta", {})

        self.log_message(f"✅ Workflow validated and fixed: {len(fixed_nodes)} nodes, {len(fixed_connections)} connections.")
        return workflow
    
    def robust_suggest_node_type(self, node_type_raw, node_name=""):
        """
        Map node type from LLM or user to a valid n8n node type using knowledge base and heuristics.
        """
        node_type_raw = (node_type_raw or "").strip().lower()
        # 1. ตรงกับ knowledge base
        for node in self.node_knowledge_base:
            if node_type_raw == node["type"].lower():
                return node["type"]
        # 2. ตรงกับชื่อ node หรือ keyword
        for node in self.node_knowledge_base:
            if node_type_raw in node["name"].lower() or node_type_raw in [kw.lower() for kw in node["keywords"]]:
                return node["type"]
        # 3. ใช้ suggest_node_type จากชื่อ node
        if hasattr(self, "suggest_node_type"):
            return self.suggest_node_type(node_name)
        # 4. fallback
        return "n8n-nodes-base.code"
    
    def convert_generic_workflow_to_n8n(self, workflow_json):
        n8n_nodes = []
        node_name_map = {}
        for i, node in enumerate(workflow_json.get("nodes", [])):
            node_id = node.get("id", f"node_{i+1}")
            node_name = node.get("name", f"Node{i+1}")

            # Map type จากข้อมูลใน node
            node_type = "n8n-nodes-base.set"
            node_type_version = 1
            parameters = {}

            # ใช้ type จาก LLM ถ้ามี
            node_type_raw = node.get("type", "").lower()
            if node_type_raw in ["task"]:
                node_type = "n8n-nodes-base.code"
                node_type_version = 2
                parameters = {
                    "language": "javascript",
                    "jsCode": f"// {node_name}\nreturn items;"
                }
            elif node_type_raw in ["decision"]:
                node_type = "n8n-nodes-base.if"
                node_type_version = 2
                parameters = {
                    "conditions": {
                        "options": {
                            "caseSensitive": True
                        }
                    }
                }
            elif node_type_raw in ["endevent", "end"]:
                node_type = "n8n-nodes-base.noOp"
                node_type_version = 1
                parameters = {}

            # เพิ่มเติม: map จากชื่อ node
            if "webhook" in node_name.lower():
                node_type = "n8n-nodes-base.webhook"
                node_type_version = 1
                parameters = {"httpMethod": "POST", "path": "webhook-path"}

            n8n_node = {
                "id": node_id,
                "name": node_name,
                "type": node_type,
                "typeVersion": node_type_version,
                "position": [100 + i*400, 200],
                "parameters": parameters
            }
            n8n_nodes.append(n8n_node)
            node_name_map[node_name] = node_id

        # สร้าง connections (map sourceRef/targetRef → name)
        connections = {}
        node_names = {i: n.get("name", f"Node{i}") for i, n in enumerate(workflow_json.get("nodes", []))}
        for edge in workflow_json.get("edges", []):
            src = edge.get("source")
            tgt = edge.get("target")
            src_name = src if isinstance(src, str) else node_names.get(src, str(src))
            tgt_name = tgt if isinstance(tgt, str) else node_names.get(tgt, str(tgt))
            if src_name and tgt_name:
                if src_name not in connections:
                    connections[src_name] = {"main": [[]]}
                connections[src_name]["main"][0].append({"node": tgt_name, "type": "main", "index": 0})
        return {
            "meta": {"instanceId": "converted"},
            "nodes": n8n_nodes,
            "connections": connections
        }
    def convert_edges_to_connections(self, workflow_json):
        """
        แปลง edges (source/target) เป็น n8n connections
        """
        nodes = workflow_json.get("nodes", [])
        edges = workflow_json.get("edges", [])
        node_names = {i: n.get("name", f"Node{i}") for i, n in enumerate(nodes)}
        connections = {}
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            src_name = src if isinstance(src, str) else node_names.get(src, str(src))
            tgt_name = tgt if isinstance(tgt, str) else node_names.get(tgt, str(tgt))
            if src_name and tgt_name:
                if src_name not in connections:
                    connections[src_name] = {"main": [[]]}
                connections[src_name]["main"][0].append({"node": tgt_name, "type": "main", "index": 0})
        workflow_json["connections"] = connections
        return workflow_json
    
    def json_schema_to_n8n_workflow(self, schema_json):
        self.selected_model = self.model_dropdown.get()
        self.ollama_url = self.ollama_url_entry.get().strip()
        diagram_description = self.diagram_description_entry.get("1.0", "end").strip()
        prompt = self.build_n8n_workflow_prompt(schema_json, diagram_description)
        payload = {
        "model": self.selected_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 2048}
        }
        try:
        # แก้ไขตรงนี้ - เพิ่ม /api/generate ให้ครบ
            response = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=600)
        
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
            
            data = response.json()
            workflow_str = data.get('response', '')
        
            self.log_message(f"DeepSeek (workflow) response length: {len(workflow_str)}")
            self.log_message(f"First 200 chars: {workflow_str[:200]}...")
        
        # Save raw response for debugging
            with open("deepseek_workflow_raw_response.txt", "w", encoding="utf-8") as f:
                f.write(workflow_str)
        
        # Show raw response popup for debugging
            self.show_raw_response_popup(workflow_str, title="⚙️ DeepSeek - Workflow JSON Response")
            workflow_json = self.extract_json_from_response(workflow_str)
            if workflow_json is None:
                self.log_message("❌ Failed to extract valid JSON from DeepSeek workflow response")
                messagebox.showerror("Analysis Error", 
                    f"Failed to extract JSON from LLM workflow response.\n"
                    f"Raw response saved to 'deepseek_workflow_raw_response.txt'")
                return None
            
            if workflow_json is not None:
                if "workflow" in workflow_json:
                    workflow_json = workflow_json["workflow"]
                if "workflows" in workflow_json and isinstance(workflow_json["workflows"], list):
                    workflow_json = workflow_json["workflows"][0]
                if "connections" not in workflow_json and "nodes" in workflow_json:
                    workflow_json = self.convert_generic_workflow_to_n8n(workflow_json)
                if "edges" in workflow_json and "connections" not in workflow_json:
                    workflow_json = self.convert_edges_to_connections(workflow_json)
            if not isinstance(workflow_json, dict):
                self.log_message("❌ Workflow JSON is not a dict, using fallback workflow.")
                workflow_json = self.create_fallback_n8n_workflow({}, "fallback")
                workflow = self.postprocess_node_types(workflow)
            if "name" not in workflow_json:
                workflow_json["name"] = "Generated Workflow"
            if "connections" in workflow_json:
                workflow_json["connections"] = self.normalize_connections(workflow_json["connections"])
            if "connections" not in workflow_json and "nodes" in workflow_json:
                workflow_json = self.convert_generic_workflow_to_n8n(workflow_json)
            if "connections" in workflow_json:
                workflow_json["connections"] = self.normalize_connections(workflow_json["connections"])
            self.log_message(f"✅ Successfully parsed n8n workflow JSON")
            if "name" not in workflow_json or not workflow_json["name"]:
                workflow_json["name"] = "Auto Generated Workflow"
            return workflow_json
        
        except Exception as e:
            self.log_message(f"❌ DeepSeek workflow failed: {str(e)}")
            messagebox.showerror("Analysis Error", f"DeepSeek workflow failed: {str(e)}")
            return None

    def check_ollama_connection(self):
        def _check():
            try:
                response = requests.get(f"{self.ollama_url}/api/tags", timeout=10)
                if response.status_code == 200:
                    tags = response.json().get("models", [])
                    self.available_models = [m["name"] for m in tags]
                    # อัปเดต UI ผ่าน main thread
                    self.root.after(0, self._update_connection_success)
                else:
                    self.root.after(0, lambda: self.connection_status.configure(text="❌ Connection failed", text_color="red"))
            except Exception as e:
                self.root.after(0, lambda: self.connection_status.configure(text=f"❌ Connection error: {str(e)}", text_color="red"))
        threading.Thread(target=_check, daemon=True).start()

    def test_ollama_connection(self):
        """Test connection to Ollama"""
        try:
            self.ollama_url = self.ollama_url_entry.get().strip()
            self.connection_status.configure(text="Testing connection...")

            response = requests.get(f"{self.ollama_url}/api/tags", timeout=600)

            if response.status_code == 200:
                data = response.json()
                self.available_models = [model['name'] for model in data.get('models', [])]

                self.root.after(0, self._update_connection_success)

            else:
                raise Exception(f"HTTP {response.status_code}")


        except Exception as e:
            error_msg = f"❌ Connection failed: {str(e)}"
            self.root.after(0, lambda: self.connection_status.configure(text=error_msg, text_color="red"))
            self.root.after(0, lambda: self.available_models_label.configure(text="Available models: Connection failed"))
            self.log_message(f"❌ Ollama connection failed: {str(e)}")

            
    def _update_connection_success(self):
        """Update UI after successful connection"""
        self.connection_status.configure(text="✅ Connection successful", text_color="green")
        self.available_models_label.configure(text=f"Available models: {', '.join(self.available_models)}")
        
        if self.available_models:
            self.model_dropdown.configure(values=self.available_models)
            if "deepseek-coder:6.7b" in self.available_models:
                self.model_dropdown.set("deepseek-coder:6.7b")

        self.log_message(f"✅ Connected to Ollama. Found {len(self.available_models)} models.")

    @staticmethod
    def build_n8n_workflow_prompt(json_schema: dict, diagram_description: str = "", logic_text: str = "") -> str:
        n8n_nodes_context = """
n8n REAL Node Types (Only use these actual working nodes):

TRIGGER NODES:
    Manual Trigger: Manually start workflows
        - type: "n8n-nodes-base.manualTrigger"
        - typeVersion: 1
        - parameters: {}

    Webhook: Receive HTTP requests
        - type: "n8n-nodes-base.webhook" 
        - typeVersion: 1
        - parameters: {"httpMethod": "POST", "path": "webhook-path"}

    Cron: Scheduled trigger
        - type: "n8n-nodes-base.cron"
        - typeVersion: 1
        - parameters: {"triggerTimes": [{"hour": 0, "minute": 0}]}

CORE PROCESSING NODES:
    HTTP Request: Call external APIs
        - type: "n8n-nodes-base.httpRequest"
        - typeVersion: 4.1
        - parameters: {"method": "GET", "url": "https://api.example.com"}

    Set: Transform/set data fields
        - type: "n8n-nodes-base.set"
        - typeVersion: 3.2
        - parameters: {"assignments": {"values": []}}

    Code: Run JavaScript/Python code
        - type: "n8n-nodes-base.code"
        - typeVersion: 2
        - parameters: {"language": "javascript", "jsCode": "return items;"}

    IF: Conditional branching
        - type: "n8n-nodes-base.if"
        - typeVersion: 2
        - parameters: {"conditions": {"options": {"caseSensitive": true}}}

    Switch: Multi-branch logic
        - type: "n8n-nodes-base.switch"
        - typeVersion: 2
        - parameters: {"property": "fieldName", "rules": []}

    Merge: Combine data streams
        - type: "n8n-nodes-base.merge"
        - typeVersion: 2.1
        - parameters: {"mode": "multiplex"}

    SplitInBatches: Batch processing
        - type: "n8n-nodes-base.splitInBatches"
        - typeVersion: 1
        - parameters: {"batchSize": 100}

    Wait: Pause workflow
        - type: "n8n-nodes-base.wait"
        - typeVersion: 1
        - parameters: {"waitFor": "1h"}

    NoOp: End step
        - type: "n8n-nodes-base.noOp"
        - typeVersion: 1
        - parameters: {}

INTEGRATION NODES:
    Email: Send email
        - type: "n8n-nodes-base.emailSend"
        - typeVersion: 1
        - parameters: {"to": "", "subject": "", "text": ""}

    Slack: Send Slack message
        - type: "n8n-nodes-base.slack"
        - typeVersion: 1
        - parameters: {"channel": "", "text": ""}

    Google Sheets: Read/write Google Sheets
        - type: "n8n-nodes-base.googleSheets"
        - typeVersion: 1
        - parameters: {"operation": "append", ...}

    Notion: Interact with Notion
        - type: "n8n-nodes-base.notion"
        - typeVersion: 1
        - parameters: {"resource": "page", ...}

    Airtable: Interact with Airtable
        - type: "n8n-nodes-base.airtable"
        - typeVersion: 1
        - parameters: {"baseId": "", ...}

    MySQL: Query MySQL database
        - type: "n8n-nodes-base.mySql"
        - typeVersion: 1
        - parameters: {"query": ""}

    Postgres: Query PostgreSQL database
        - type: "n8n-nodes-base.postgres"
        - typeVersion: 1
        - parameters: {"query": ""}

AI/LLM NODES (Working ones only):
    OpenAI: Chat completions and vision
        - type: "@n8n/n8n-nodes-langchain.openAi"
        - typeVersion: 1.3
        - parameters: {"model": "gpt-4o", "options": {}}

    Anthropic: Claude integration  
        - type: "@n8n/n8n-nodes-langchain.anthropic"
        - typeVersion: 1.1
        - parameters: {"model": "claude-3-sonnet-20240229"}

IMPORTANT: Only use these exact node types and typeVersions. Do not invent nodes that don't exist.
"""
        example_flowchart = """
Example Flowchart:
Start
User submits form
Validate input
If valid
Send email
End

Corresponding n8n JSON:
{
  "nodes": [
    {
      "id": "1",
      "name": "Webhook Trigger",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 1,
      "position": [100, 200],
      "parameters": {"httpMethod": "POST", "path": "submit-form"}
    },
    {
      "id": "2",
      "name": "Validate Input",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [500, 200],
      "parameters": {"language": "javascript", "jsCode": "// validate input\nreturn items;"}
    },
    {
      "id": "3",
      "name": "If Valid",
      "type": "n8n-nodes-base.if",
      "typeVersion": 2,
      "position": [900, 200],
      "parameters": {"conditions": {"boolean": [{"value1": "={{ $json.valid }}", "operation": "isTrue"}]}}
    },
    {
      "id": "4",
      "name": "Send Email",
      "type": "n8n-nodes-base.emailSend",
      "typeVersion": 1,
      "position": [1300, 200],
      "parameters": {"to": "user@example.com", "subject": "Success", "text": "Your form is valid!"}
    }
  ],
  "connections": {
    "Webhook Trigger": {"main": [[{"node": "Validate Input", "type": "main", "index": 0}]]},
    "Validate Input": {"main": [[{"node": "If Valid", "type": "main", "index": 0}]]},
    "If Valid": {"main": [[{"node": "Send Email", "type": "main", "index": 0}]]}
  }
}
"""

        prompt = (
    "You are an n8n workflow generator. You MUST analyze the SPECIFIC content in the provided schema and create a UNIQUE workflow that matches that exact process.\n\n"
    "- Each node must have a unique id (do not use \"uuid-string\", use \"node1\", \"node2\", ...).\n"
    "- Do not include any special tokens or artifacts.\n"
    "- Do not include markdown, comments, or explanations.\n"
    "- Output ONLY valid JSON. No explanations, no markdown, no code blocks.\n"
    "- Use only real node types from the list above.\n"
    "If you cannot map a step to a real n8n node, use a Code node (type: \"n8n-nodes-base.code\") and write a comment in the jsCode field describing what should happen.\n"
    "Never apologize. Always output a valid n8n workflow JSON, even if some steps are only placeholders.\n"

    "CRITICAL RULES:\n"
    "• NEVER create generic or template workflows\n"
    "• EACH different schema must produce a DIFFERENT workflow\n"
    "• READ and UNDERSTAND the specific processes shown in the schema\n"
    "• CREATE nodes with names and logic that match the diagram content\n"
    "• NEVER ask for clarification or additional information\n"
    "• ONLY use the exact node types and typeVersions provided below\n"
    "• ALWAYS create a complete, working n8n workflow JSON\n\n"
    + n8n_nodes_context +
    "\n\nWORKFLOW CREATION APPROACH:\n"
    "1. Start with Manual Trigger (always safe)\n"
    "2. Analyze the schema to understand the data flow\n"
    "3. Use HTTP Request for external APIs\n"
    "4. Use Set nodes for data transformation\n"
    "5. Use Code nodes for complex logic\n"
    "6. Use IF nodes for decision points\n"
    "7. Use OpenAI/Anthropic only for AI processing\n\n"
    "SCHEMA ANALYSIS - CRITICAL:\n"
    "You MUST analyze the specific content in the provided schema/diagram:\n"
    "• READ every element, shape, text, arrow, and connection in the schema\n"
    "• IDENTIFY the exact steps, processes, and decision points shown\n"
    "• UNDERSTAND the data flow and business logic depicted\n"
    "• CREATE nodes that match the SPECIFIC processes shown in the diagram\n"
    "• DO NOT use generic templates - each diagram should produce DIFFERENT workflows\n\n"
    "CONTENT-SPECIFIC MAPPING:\n"
    "• Process boxes with specific names → Code/Set nodes with that exact functionality\n"
    "• Decision diamonds with conditions → IF nodes with those specific conditions\n"
    "• Data stores/databases → HTTP Request nodes to actual endpoints\n"
    "• External systems mentioned → HTTP Request nodes to those systems\n"
    "• User interactions → Manual Trigger or Webhook nodes\n"
    "• API calls shown → HTTP Request nodes with specific APIs\n"
    "• Calculations/formulas → Code nodes with actual calculation logic\n"
    "• AI/ML processes → OpenAI/Anthropic nodes with specific prompts\n\n"
    "DYNAMIC WORKFLOW CREATION:\n"
    "• Node names must reflect the actual process names from the diagram\n"
    "• Parameters must be relevant to the specific use case shown\n"
    "• Connections must follow the exact flow shown in the diagram\n"
    "• If diagram shows 3 steps, create 3 nodes (plus trigger)\n"
    "• If diagram shows 8 steps, create 8 nodes (plus trigger)\n"
    "• Each diagram element should have a corresponding workflow element\n\n"
    "You are an n8n workflow generator. ...\n"
    "Here is an example:\n"
    f"{example_flowchart}\n"
    "Now, generate the n8n JSON for the following process:\n"
    f"{logic_text}\n"
    "Output only the JSON object, nothing else."

    "JSON STRUCTURE REQUIREMENTS:\n"
    "{\n"
    '  "meta": {"instanceId": "123"},\n'
    '  "nodes": [\n'
    '    {\n'
    '      "id": "uuid-string",\n'
    '      "name": "Node Name",\n'
    '      "type": "exact-node-type-from-list-above",\n'
    '      "typeVersion": number,\n'
    '      "position": [x, y],\n'
    '      "parameters": {}\n'
    '    }\n'
    '  ],\n'
    '  "connections": {\n'
    '    "Source Node Name": {\n'
    '      "main": [[\n'
    '        {"node": "Target Node Name", "type": "main", "index": 0}\n'
    '      ]]\n'
    '    }\n'
    '  }\n'
    '}\n\n'
    "POSITIONING:\n"
    "• Start at [100, 200]\n"
    "• Increment x by 400 for each step\n"
    "• Use y offsets for branches\n\n"
    "MANDATORY OUTPUT:\n"
    "• Output ONLY valid JSON\n"
    "• No explanations, no markdown, no code blocks\n"
    "• JSON must be directly importable to n8n\n"
    "• Use only real node types from the list above\n\n"
    f"USER DESCRIPTION:\n{diagram_description if diagram_description else '[No description provided]'}\n\n"
    f"SCHEMA TO ANALYZE AND CONVERT (Read carefully and create workflow specific to this content):\n{json.dumps(json_schema, ensure_ascii=False, indent=2)}\n\n"
    "BEFORE CREATING JSON, THINK:\n"
    "- What specific process is shown in this schema?\n"
    "- What are the unique steps and their names?\n"
    "- What decisions or conditions are present?\n"
    "- How should the data flow between steps?\n"
    "- What would make this workflow different from others?\n\n"
    "CREATE A WORKFLOW THAT IS SPECIFIC TO THIS SCHEMA - OUTPUT ONLY THE JSON WORKFLOW NOW:"
    "Only use node types and typeVersions listed below. Do not invent or hallucinate node types.\n"
)
        return prompt

    def create_workflow_from_analysis(self, analysis):
        """สร้าง workflow จากการวิเคราะห์ที่ได้ โดยวิเคราะห์ความสัมพันธ์และเลือก node ที่เหมาะสม"""
        try:
            classes = analysis.get('classes', [])
            relationships = analysis.get('relationships', [])

            # ✅ เพิ่มสองบรรทัดนี้
            flow_elements = analysis.get("flow_elements", {})
            unique_id = getattr(self, 'current_analysis_id', f"flow_{datetime.now().strftime('%H%M%S')}")

            # วิเคราะห์ความสัมพันธ์และจัดกลุ่ม classes
            workflow_analysis = self.analyze_workflow_structure(classes, relationships)
        
            # สร้าง nodes ตามการวิเคราะห์
            nodes = self.create_nodes_from_analysis(workflow_analysis, flow_elements, unique_id)
        
            # สร้าง connections ตามความสัมพันธ์
            connections = self.create_connections_from_relationships(nodes, relationships)
        
            # สร้าง workflow
            workflow = {
                "name": self.workflow_name_entry.get() if hasattr(self, "workflow_name_entry") else "Class Diagram Workflow",
                "nodes": nodes,
                "connections": connections,
                "active": False,
                "settings": {"executionOrder": "v1"},
                "version": 1,
                "meta": {
                    "templateCredsSetupCompleted": True
                }
            }
        
            self.log_message(f"✅ Generated workflow with {len(nodes)} nodes and {len(connections)} connections")
            return workflow
        
        except Exception as e:
            self.log_message(f"❌ Workflow creation error: {str(e)}")
            raise

    def analyze_workflow_structure(self, classes, relationships):
        """วิเคราะห์โครงสร้างของ workflow จาก classes และ relationships รวมถึง AI capabilities"""
        workflow_analysis = {
            'entry_points': [],      # classes ที่เป็นจุดเริ่มต้น
            'data_processors': [],   # classes ที่ประมวลผลข้อมูล
            'external_services': [], # classes ที่เชื่อมต่อกับ external services
            'storage_entities': [],  # classes ที่เก็บข้อมูล
            'output_handlers': [],   # classes ที่จัดการ output
            'ai_agents': [],         # classes ที่เหมาะกับ AI Agent
            'text_processors': [],   # classes ที่ประมวลผลข้อความ
            'decision_makers': [],   # classes ที่ตัดสินใจ
            'data_extractors': []    # classes ที่สกัดข้อมูล
        }
        
        # วิเคราะห์แต่ละ class
        for class_info in classes:
            class_name = class_info.get('name', '')
            attributes = class_info.get('attributes', [])
            methods = class_info.get('methods', [])
            responsibilities = class_info.get('responsibilities', '').lower()
            
            # ตรวจสอบว่าเป็น AI Agent
            if self.is_ai_agent_suitable(class_name, methods, responsibilities):
                workflow_analysis['ai_agents'].append(class_info)
            
            # ตรวจสอบว่าเป็น text processor
            elif self.is_text_processor(class_name, methods, responsibilities):
                workflow_analysis['text_processors'].append(class_info)
            
            # ตรวจสอบว่าเป็น decision maker
            elif self.is_decision_maker(class_name, methods, responsibilities):
                workflow_analysis['decision_makers'].append(class_info)
            
            # ตรวจสอบว่าเป็น data extractor
            elif self.is_data_extractor(class_name, methods, responsibilities):
                workflow_analysis['data_extractors'].append(class_info)
            
            # เช็ค existing categories
            elif self.is_external_service(class_name, methods, responsibilities):
                workflow_analysis['external_services'].append(class_info)
            elif self.is_data_processor(class_name, methods, responsibilities):
                workflow_analysis['data_processors'].append(class_info)
            elif self.is_storage_entity(class_name, attributes, responsibilities):
                workflow_analysis['storage_entities'].append(class_info)
            elif self.is_output_handler(class_name, methods, responsibilities):
                workflow_analysis['output_handlers'].append(class_info)
            elif self.is_entry_point(class_name, relationships, responsibilities):
                workflow_analysis['entry_points'].append(class_info)
            else:
                # default เป็น data processor
                workflow_analysis['data_processors'].append(class_info)

        return workflow_analysis

    def is_external_service(self, class_name, methods, responsibilities):
        """ตรวจสอบว่า class เป็น external service หรือไม่"""
        external_keywords = ['api', 'service', 'client', 'http', 'rest', 'web', 'request', 'call']
        method_keywords = ['get', 'post', 'put', 'delete', 'fetch', 'send', 'call', 'request']
    
        name_lower = class_name.lower()
    
    # ตรวจสอบจากชื่อ class
        if any(keyword in name_lower for keyword in external_keywords):
            return True
    
    # ตรวจสอบจาก methods
        if any(any(keyword in method.lower() for keyword in method_keywords) for method in methods):
            return True
    
    # ตรวจสอบจาก responsibilities
        if any(keyword in responsibilities for keyword in external_keywords + method_keywords):
            return True
    
        return False

    def is_data_processor(self, class_name, methods, responsibilities):
        """ตรวจสอบว่า class เป็น data processor หรือไม่"""
        processor_keywords = ['process', 'transform', 'convert', 'parse', 'validate', 'calculate', 'compute']
        method_keywords = ['process', 'transform', 'convert', 'parse', 'validate', 'calculate', 'format']
    
        name_lower = class_name.lower()
    
    # ตรวจสอบจากชื่อ class
        if any(keyword in name_lower for keyword in processor_keywords):
            return True
    
    # ตรวจสอบจาก methods
        if any(any(keyword in method.lower() for keyword in method_keywords) for method in methods):
            return True
    
    # ตรวจสอบจาก responsibilities
        if any(keyword in responsibilities for keyword in processor_keywords + method_keywords):
            return True
    
        return False

    def is_storage_entity(self, class_name, attributes, responsibilities):
        """ตรวจสอบว่า class เป็น storage entity หรือไม่"""
        storage_keywords = ['model', 'entity', 'data', 'record', 'table', 'document', 'store']
    
        name_lower = class_name.lower()
    
    # ตรวจสอบจากชื่อ class
        if any(keyword in name_lower for keyword in storage_keywords):
            return True
    
    # ตรวจสอบจาก attributes (มี attributes มาก = data entity)
        if len(attributes) > 3:
            return True
    
    # ตรวจสอบจาก responsibilities
        if any(keyword in responsibilities for keyword in storage_keywords):
            return True
    
        return False

    def is_output_handler(self, class_name, methods, responsibilities):
        """ตรวจสอบว่า class เป็น output handler หรือไม่"""
        output_keywords = ['output', 'export', 'report', 'display', 'render', 'format', 'print']
        method_keywords = ['export', 'save', 'write', 'output', 'display', 'render', 'format']
    
        name_lower = class_name.lower()
    
    # ตรวจสอบจากชื่อ class
        if any(keyword in name_lower for keyword in output_keywords):
            return True
    
    # ตรวจสอบจาก methods
        if any(any(keyword in method.lower() for keyword in method_keywords) for method in methods):
            return True
    
    # ตรวจสอบจาก responsibilities
        if any(keyword in responsibilities for keyword in output_keywords + method_keywords):
            return True
    
        return False

    def is_entry_point(self, class_name, relationships, responsibilities):
        """ตรวจสอบว่า class เป็น entry point หรือไม่"""
        entry_keywords = ['controller', 'handler', 'trigger', 'main', 'start', 'init']

        name_lower = class_name.lower()
    
    # ตรวจสอบจากชื่อ class
        if any(keyword in name_lower for keyword in entry_keywords):
            return True
    
    # ตรวจสอบจาก relationships (ไม่มี dependency จาก class อื่น)
        has_incoming_dependency = any(rel.get('to') == class_name and rel.get('type') == 'dependency' 
                                    for rel in relationships)
    
        if not has_incoming_dependency:
            return True
    
    # ตรวจสอบจาก responsibilities
        if any(keyword in responsibilities for keyword in entry_keywords):
            return True
    
        return False

    def is_ai_agent_suitable(self, class_name, methods, responsibilities):
        """ตรวจสอบว่า class เหมาะกับ AI Agent หรือไม่"""
        ai_keywords = ['intelligent', 'smart', 'ai', 'agent', 'analyze', 'recommend', 'predict', 'learn', 'understand', 'interpret']
        method_keywords = ['analyze', 'predict', 'recommend', 'understand', 'interpret', 'decide', 'evaluate', 'assess']
        
        name_lower = class_name.lower()
        
        # ตรวจสอบจากชื่อ class
        if any(keyword in name_lower for keyword in ai_keywords):
            return True
        
        # ตรวจสอบจาก methods
        if any(any(keyword in method.lower() for keyword in method_keywords) for method in methods):
            return True
        
        # ตรวจสอบจาก responsibilities
        if any(keyword in responsibilities for keyword in ai_keywords + method_keywords):
            return True
        
        return False

    def is_text_processor(self, class_name, methods, responsibilities):
        """ตรวจสอบว่า class ประมวลผลข้อความหรือไม่"""
        text_keywords = ['text', 'string', 'message', 'content', 'document', 'parse', 'extract', 'classify']
        method_keywords = ['parse', 'extract', 'classify', 'tokenize', 'analyze', 'process']
        
        name_lower = class_name.lower()
        
        if any(keyword in name_lower for keyword in text_keywords):
            return True
        
        if any(any(keyword in method.lower() for keyword in method_keywords) for method in methods):
            return True
        
        return False

    def choose_trigger_node(self, flow_elements):
        processes = flow_elements.get("processes", [])
        desc = processes[0].lower() if processes else " ".join(flow_elements.get("start_end", [])).lower()

        # เงื่อนไขที่ควรใช้ Manual Trigger จริงๆ
        if any(kw in desc for kw in ["manual", "start", "begin", "test", "user click"]):
            return {
                "type": "n8n-nodes-base.manualTrigger",
                "typeVersion": 1,
                "parameters": {},
                "name": "Manual Trigger"
            }
        # ถ้าไม่เจอ keyword ที่ชัดเจน → ใช้ Webhook เป็น default
        return {
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 1,
            "parameters": {"httpMethod": "POST", "path": "webhook-path"},
            "name": "Webhook Trigger"
        }
    def postprocess_node_types(self, workflow):
        """
        Map generic or hallucinated node types (e.g. 'start', 'decision', 'code') 
        to real n8n node types in-place.
        """
        for node in workflow.get("nodes", []):
            # ถ้า type เป็น set/function/code หรือ generic ให้ลอง map ใหม่
            suggested_type = self.suggest_node_type(
                node.get("name", "") + " " + node.get("description", "")
            )
            if node["type"] not in self.get_valid_node_types() or node["type"] != suggested_type:
                self.log_message(f"🔄 Mapping node '{node['name']}' from {node['type']} → {suggested_type}")
                node["type"] = suggested_type
        return workflow
    
    def is_decision_maker(self, class_name, methods, responsibilities):
        """ตรวจสอบว่า class ตัดสินใจหรือไม่"""
        decision_keywords = ['decision', 'decide', 'choose', 'select', 'evaluate', 'assess', 'judge', 'determine']
        method_keywords = ['decide', 'choose', 'select', 'evaluate', 'assess', 'determine', 'approve', 'reject']
        
        name_lower = class_name.lower()
        
        if any(keyword in name_lower for keyword in decision_keywords):
            return True
        
        if any(any(keyword in method.lower() for keyword in method_keywords) for method in methods):
            return True
        
        if any(keyword in responsibilities for keyword in decision_keywords + method_keywords):
            return True
        
        return False

    def is_data_extractor(self, class_name, methods, responsibilities):
        """ตรวจสอบว่า class สกัดข้อมูลหรือไม่"""
        extract_keywords = ['extract', 'scrape', 'mine', 'harvest', 'collect', 'gather', 'retrieve']
        method_keywords = ['extract', 'scrape', 'mine', 'collect', 'gather', 'retrieve', 'fetch', 'obtain']
        
        name_lower = class_name.lower()
        
        if any(keyword in name_lower for keyword in extract_keywords):
            return True
        
        if any(any(keyword in method.lower() for keyword in method_keywords) for method in methods):
            return True
        
        if any(keyword in responsibilities for keyword in extract_keywords + method_keywords):
            return True
        
        return False

    def create_nodes_from_analysis(self, workflow_analysis, flow_elements, unique_id):
        """สร้าง nodes จากการวิเคราะห์ workflow รวมถึง AI nodes"""
        nodes = []
        x_pos = 100
        y_pos = 100
        
        trigger_node = self.choose_trigger_node(flow_elements)
        trigger_node["id"] = f"{unique_id}-start"
        trigger_node["position"] = [100, 200]
        nodes.append(trigger_node)
        previous_node = trigger_node["name"]
        x_pos += 400

        # 2. สร้าง nodes จาก process/steps
        for i, process in enumerate(flow_elements.get("processes", [])):
            node_type = self.suggest_node_type(process)
            node_id = f"{unique_id}-process-{i}"
            node_name = process.title()
            node = {
                "id": node_id,
                "name": node_name,
                "type": node_type,
                "typeVersion": 1,
                "position": [x_pos, y_pos],
                "parameters": {}
            }
            # เพิ่ม parameters เฉพาะ node type
            if node_type == "n8n-nodes-base.code":
                node["typeVersion"] = 2
                node["parameters"] = {
                    "language": "javascript",
                    "jsCode": f"// {process}\nreturn items;"
                }
            elif node_type == "n8n-nodes-base.set":
                node["parameters"] = {
                    "values": {
                        "string": [
                            {"name": "step", "value": process}
                        ]
                    }
                }
            # ...เพิ่ม logic สำหรับ node type อื่นๆ ตามต้องการ...

            nodes.append(node)
            previous_node = node_name
            x_pos += 400
        

        # สร้าง nodes สำหรับ entry points
        for class_info in workflow_analysis['entry_points']:
            node = self.create_function_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            x_pos += 250
        
        # สร้าง AI Agent nodes
        for class_info in workflow_analysis['ai_agents']:
            node = self.create_ai_agent_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        # สร้าง Text Classifier nodes
        for class_info in workflow_analysis['text_processors']:
            node = self.create_text_classifier_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        # สร้าง Information Extractor nodes
        for class_info in workflow_analysis['data_extractors']:
            node = self.create_information_extractor_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        # สร้าง Ollama Chat nodes สำหรับ decision makers
        for class_info in workflow_analysis['decision_makers']:
            node = self.create_ollama_chat_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        # สร้าง nodes สำหรับ external services
        for class_info in workflow_analysis['external_services']:
            node = self.create_http_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        # สร้าง nodes สำหรับ data processors
        for class_info in workflow_analysis['data_processors']:
            node = self.create_function_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        # สร้าง nodes สำหรับ storage entities
        for class_info in workflow_analysis['storage_entities']:
            node = self.create_set_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        # สร้าง nodes สำหรับ output handlers
        x_pos += 250
        y_pos = 100
        for class_info in workflow_analysis['output_handlers']:
            node = self.create_function_node(class_info, [x_pos, y_pos])
            nodes.append(node)
            y_pos += 150
        
        return nodes

    def create_http_node(self, class_info, position):
        """สร้าง HTTP Request node"""
    # สร้าง URL จาก class info
        base_url = "https://api.example.com"
        endpoint = f"/{class_info.get('name', 'data').lower()}"
    
        return {
            "parameters": {
            "url": f"{base_url}{endpoint}",
            "method": "GET",
            "options": {},
            "headers": {
                "Content-Type": "application/json"
            }
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 3,
        "position": position,
        "id": str(uuid.uuid4()),
        "name": f"HTTP {class_info.get('name', 'Request')}"
    }

    def create_function_node(self, class_info, position):
        """สร้าง Function node"""
    # สร้าง JavaScript code จาก class info
        methods = class_info.get('methods', [])
        attributes = class_info.get('attributes', [])
    
        js_code = f"""
// Process {class_info.get('name', 'Data')}
const inputData = items[0].json;

// Simulate methods: {', '.join(methods)}
const processedData = {{
  className: '{class_info.get('name', 'Unknown')}',
  attributes: {json.dumps(attributes)},
  methods: {json.dumps(methods)},
  processedAt: new Date().toISOString(),
  ...inputData
}};

return [{{ json: processedData }}];
"""
    
        return {
        "parameters": {
            "functionCode": js_code.strip()
        },
        "type": "n8n-nodes-base.function",
        "typeVersion": 1,
        "position": position,
        "id": str(uuid.uuid4()),
        "name": f"Process {class_info.get('name', 'Data')}"
    }

    def create_set_node(self, class_info, position):
        """สร้าง Set node"""
        attributes = class_info.get('attributes', [])
        methods = class_info.get('methods', [])
    
    # สร้าง values สำหรับ Set node
        values = {
        "string": [
            {"name": "className", "value": class_info.get('name', 'Unknown')},
            {"name": "attributes", "value": ", ".join(attributes)},
            {"name": "methods", "value": ", ".join(methods)},
            {"name": "responsibilities", "value": class_info.get('responsibilities', '')}
        ]
    }
    
        return {
        "parameters": {"values": values},
        "type": "n8n-nodes-base.set",
        "typeVersion": 1,
        "position": position,
        "id": str(uuid.uuid4()),
        "name": f"Set {class_info.get('name', 'Data')}"
    }

    def create_ai_agent_node(self, class_info, position, agent_type="conversationalAgent"):
        """สร้าง AI Agent node"""
        class_name = class_info.get('name', 'Unknown')
        methods = class_info.get('methods', [])
        responsibilities = class_info.get('responsibilities', '')

        # สร้าง system prompt จาก class info
        system_prompt = f"""You are an AI agent representing the {class_name} class.

        Your responsibilities: {responsibilities}

        Available methods: {', '.join(methods)}

        Process the input data according to your class responsibilities and return structured results."""

        return {
            "parameters": {
                "agent": {
                    "type": agent_type,
                    "maxIterations": 10,  
                    "llm": {
                        "@type": "openai",
                        "model": "gpt-3.5-turbo",
                        "temperature": 0.2,
                        "maxTokens": 1000
                    },
                    "memory": "@type:bufferMemory",
                    "systemMessage": system_prompt
                },
                "input": "={{ $json.input || 'Process the provided data' }}",
                "tools": []
            },
            "type": "@n8n/n8n-nodes-langchain.agent",
            "typeVersion": 1,
            "position": position,
            "id": str(uuid.uuid4()),
            "name": f"AI Agent: {class_name}"
        }

    def create_text_classifier_node(self, class_info, position):
        """สร้าง Text Classifier node"""
        class_name = class_info.get('name', 'Unknown')

        # สร้าง categories จาก methods หรือ attributes
        methods = class_info.get('methods', [])
        categories = methods[:5] if methods else ["valid", "invalid", "pending", "completed", "error"]

        return {
            "parameters": {
                "model": {
                    "@type": "openai",
                    "model": "gpt-3.5-turbo",
                    "temperature": 0.1
                },
                "categories": categories,
                "text": "={{ $json.text || $json.input }}"
            },
            "type": "@n8n/n8n-nodes-langchain.textClassifier",
            "typeVersion": 1,
            "position": position,
            "id": str(uuid.uuid4()),
            "name": f"Classify: {class_name}"
        }

    def compare_models_workflow(self, flowchart_text):
        models_to_try = ['deepseek-coder:6.7b', 'llama3:8b', 'phi3:instruct']
        self.ollama_url = self.ollama_url_entry.get().strip()
        prompt = self.build_prompt(flowchart_text)
        results = {}

        for model in models_to_try:
            try:
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 4096,
                        "top_p": 0.8,
                        "repeat_penalty": 1.1
                    }
                }
                r = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=600)
                data = r.json()
                response_text = data.get("response", "")
                workflow_json = self.extract_json_from_response(response_text)
                results[model] = workflow_json or response_text
            except Exception as e:
                results[model] = f"❌ Failed: {str(e)}"

        self.show_model_comparison_popup(results)


    def show_model_comparison_popup(self, results):
        popup = tk.Toplevel(self.root)
        popup.title("🧠 Compare Model Outputs")
        popup.geometry("1200x700")

        for i, (model, content) in enumerate(results.items()):
            frame = tk.Frame(popup)
            frame.grid(row=0, column=i, padx=10, pady=10)
            label = tk.Label(frame, text=model, font=("Arial", 12, "bold"))
            label.pack()

            text_widget = tk.Text(frame, width=60, height=35, wrap="word")
            text_widget.pack()
            text_widget.insert("1.0", json.dumps(content, indent=2) if isinstance(content, dict) else content)

            def copy_and_use(content=content):
                self.generated_workflow = content
                self.upload_n8n_btn.configure(state="normal")
                self.display_workflow(content)
                popup.destroy()

            tk.Button(frame, text="✅ Use This", command=copy_and_use).pack(pady=5)


    def create_information_extractor_node(self, class_info, position):
        """สร้าง Information Extractor node"""
        class_name = class_info.get('name', 'Unknown')
        attributes = class_info.get('attributes', [])

        # สร้าง extraction schema จาก attributes
        schema = {
            "type": "object",
            "properties": {}
        }

        for attr in attributes:
            schema["properties"][attr] = {
                "type": "string",
                "description": f"Extract {attr} information"
            }

        return {
            "parameters": {
                "model": {
                    "@type": "openai", 
                    "model": "gpt-3.5-turbo",
                    "temperature": 0.1
                },
                "schema": schema,
                "text": "={{ $json.text || $json.input }}"
            },
            "type": "@n8n/n8n-nodes-langchain.informationExtractor",
            "typeVersion": 1,
            "position": position,
            "id": str(uuid.uuid4()),
            "name": f"Extract: {class_name}"
        }

    def create_ollama_chat_node(self, class_info, position):
        """สร้าง Ollama Chat Model node สำหรับ local AI"""
        class_name = class_info.get('name', 'Unknown')
        responsibilities = class_info.get('responsibilities', '')

        system_message = f"You are processing data for {class_name}. {responsibilities}"

        return {
            "parameters": {
                "model": "llama2:7b",  # หรือ model อื่นที่มีใน Ollama
                "baseURL": "http://localhost:11434",
                "temperature": 0.3,
                "messages": [
                    {
                        "role": "system",
                        "content": system_message
                    },
                    {
                        "role": "user", 
                        "content": "={{ $json.input || $json.query }}"
                    }
                ]
            },
            "type": "@n8n/n8n-nodes-langchain.lmChatOllama",
            "typeVersion": 1,
            "position": position,
            "id": str(uuid.uuid4()),
            "name": f"Ollama Chat: {class_name}"
        }

    
    def create_connections_from_relationships(self, nodes, relationships):
        """สร้าง connections จาก relationships"""
        connections = {}
    
    # สร้าง mapping ระหว่าง class name กับ node name
        class_to_node = {}
        for node in nodes:
            node_name = node['name']
        # Extract class name from node name
            if 'HTTP ' in node_name:
                class_name = node_name.replace('HTTP ', '')
            elif 'Process ' in node_name:
                class_name = node_name.replace('Process ', '')
            elif 'Set ' in node_name:
                class_name = node_name.replace('Set ', '')
            else:
                class_name = node_name
        
            class_to_node[class_name] = node_name
    
    # เชื่อม trigger กับ node แรก (ถ้ามี)
        if len(nodes) > 1:
            connections[nodes[0]['name']] = {
            "main": [[{"node": nodes[1]['name'], "type": "main", "index": 0}]]
            }
    
    # สร้าง connections จาก relationships
        for rel in relationships:
            from_class = rel.get('from', '')
            to_class = rel.get('to', '')
            rel_type = rel.get('type', '')
        
        # หา node names ที่ตรงกับ class names
            from_node = class_to_node.get(from_class)
            to_node = class_to_node.get(to_class)
        
            if from_node and to_node and from_node != to_node:
            # สร้าง connection ตาม relationship type
                if rel_type in ['dependency', 'association', 'composition']:
                    if from_node not in connections:
                        connections[from_node] = {"main": [[]]}
                
                    connections[from_node]["main"][0].append({
                    "node": to_node,
                    "type": "main",
                    "index": 0
                    })
    
    # เชื่อม nodes ที่ไม่มี relationship แบบ sequential
        connected_nodes = set()
        for conn in connections.values():
            for main_conn in conn.get("main", [[]]):
                for target in main_conn:
                    connected_nodes.add(target["node"])
    
    # เชื่อม nodes ที่ยังไม่ได้เชื่อม
        previous_node = None
        for node in nodes[1:]:  # ข้าม trigger node
            node_name = node['name']
            if node_name not in connected_nodes and previous_node:
                if previous_node not in connections:
                    connections[previous_node] = {"main": [[]]}
            
                connections[previous_node]["main"][0].append({
                "node": node_name,
                "type": "main",
                "index": 0
                })
            previous_node = node_name
    
        return connections


    def setup_config_tab(self):
        """Setup the configuration tab"""
        tab = self.notebook.add("⚙️ Configuration")
        
        # Scrollable frame
        scrollable_frame = ctk.CTkScrollableFrame(tab)
        scrollable_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Ollama Configuration
        ollama_frame = ctk.CTkFrame(scrollable_frame)
        ollama_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(ollama_frame, text="Ollama Configuration", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        
        # Ollama URL
        url_frame = ctk.CTkFrame(ollama_frame)
        url_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(url_frame, text="Ollama URL:").pack(anchor="w")
        self.ollama_url_entry = ctk.CTkEntry(url_frame, placeholder_text="http://localhost:11434")
        self.ollama_url_entry.pack(fill="x", pady=5)
        self.ollama_url_entry.insert(0, self.ollama_url)
        self.ollama_url_entry.bind("<Return>", lambda e: self.check_ollama_connection())
        
        # Test connection button
        self.test_connection_btn = ctk.CTkButton(ollama_frame, text="🔗 Test Connection",
                                               command=self.test_ollama_connection)
        self.test_connection_btn.pack(pady=10)
        
        # Connection status
        self.connection_status = ctk.CTkLabel(ollama_frame, text="Connection not tested")
        self.connection_status.pack(pady=5)
        
        # Model Selection
        model_frame = ctk.CTkFrame(scrollable_frame)
        model_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(model_frame, text="AI Model Selection", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        
        # Model dropdown
        self.model_dropdown = ctk.CTkComboBox(model_frame, values=AVAILABLE_OLLAMA_MODELS)
        self.model_dropdown.pack(fill="x", padx=20, pady=10)
        self.model_dropdown.set("llava:13b" if "llava:13b" in AVAILABLE_OLLAMA_MODELS else AVAILABLE_OLLAMA_MODELS[0])
        
        # Available models display
        self.available_models_label = ctk.CTkLabel(model_frame, text="Available models: Not checked")
        self.available_models_label.pack(pady=10)
        compare_btn = ctk.CTkButton(tab, text="🧪 Compare Multiple Models", command=lambda: self.compare_models_workflow(self.analyze_with_llava(self.uploaded_image_path)))
        compare_btn.pack(pady=(0, 10))
        # n8n Configuration
        n8n_frame = ctk.CTkFrame(scrollable_frame)
        n8n_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(n8n_frame, text="n8n Configuration", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        
        # n8n URL
        n8n_url_frame = ctk.CTkFrame(n8n_frame)
        n8n_url_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(n8n_url_frame, text="n8n Instance URL:").pack(anchor="w")
        self.n8n_url_entry = ctk.CTkEntry(n8n_url_frame, placeholder_text="http://localhost:5678")
        self.n8n_url_entry.pack(fill="x", pady=5)
        self.n8n_url_entry.insert(0, self.n8n_url)
        
        # n8n API Key
        n8n_key_frame = ctk.CTkFrame(n8n_frame)
        n8n_key_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(n8n_key_frame, text="API Key (optional):").pack(anchor="w")
        self.n8n_api_key_entry = ctk.CTkEntry(n8n_key_frame, placeholder_text="Enter your n8n API key", show="*")
        self.n8n_api_key_entry.pack(fill="x", pady=5)

        ai_frame = ctk.CTkFrame(scrollable_frame)
        ai_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(ai_frame, text="AI Configuration", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)

        # AI Provider Selection
        ai_provider_frame = ctk.CTkFrame(ai_frame)
        ai_provider_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(ai_provider_frame, text="Preferred AI Provider:").pack(anchor="w")
        self.ai_provider_dropdown = ctk.CTkComboBox(ai_provider_frame, 
                                                values=["OpenAI", "Anthropic", "Ollama (Local)", "Auto"])
        self.ai_provider_dropdown.pack(fill="x", pady=5)
        self.ai_provider_dropdown.set("Ollama (Local)")

        # AI Model Selection
        ai_model_frame = ctk.CTkFrame(ai_frame)
        ai_model_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(ai_model_frame, text="AI Model:").pack(anchor="w")
        self.ai_model_entry = ctk.CTkEntry(ai_model_frame, placeholder_text="gpt-3.5-turbo or llama2:7b")
        self.ai_model_entry.pack(fill="x", pady=5)
        self.ai_model_entry.insert(0, "llama2:7b")

        # AI Temperature
        ai_temp_frame = ctk.CTkFrame(ai_frame)
        ai_temp_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(ai_temp_frame, text="AI Temperature (0.0-1.0):").pack(anchor="w")
        self.ai_temperature_slider = ctk.CTkSlider(ai_temp_frame, from_=0.0, to=1.0, number_of_steps=10)
        self.ai_temperature_slider.pack(fill="x", pady=5)
        self.ai_temperature_slider.set(0.3)

        # AI Instructions
        ai_instructions_frame = ctk.CTkFrame(ai_frame)
        ai_instructions_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(ai_instructions_frame, text="วิธีใช้ AI Agents ใน n8n:", 
                    font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(10,5))

        instructions_text = """
        1. AI Agent Node: ใช้สำหรับงานที่ซับซ้อน ต้องการการคิดและตัดสินใจ
        - conversationalAgent: สำหรับการสนทนา
        - toolsAgent: เมื่อต้องการให้ AI ใช้เครื่องมือ
        - planAndExecuteAgent: สำหรับงานหลายขั้นตอน

        2. Text Classifier: จำแนกประเภทข้อความ
        - เหมาะสำหรับการแยกประเภท email, ความคิดเห็น, เอกสาร

        3. Information Extractor: สกัดข้อมูลจากข้อความ
        - แยกข้อมูลจากเอกสาร, อีเมล, หรือข้อความอิสระ

        4. Ollama Chat: ใช้ AI model ที่ติดตั้งใน local
        - ไม่ต้องจ่ายค่า API, ข้อมูลไม่ออกจากเครื่อง

        5. การใช้ Tools กับ AI Agent:
        - Calculator: คำนวณ
        - Code Interpreter: รันโค้ด Python  
        - HTTP Request Tool: เรียก API
        - Wikipedia: ค้นหาข้อมูล
        """

        instructions_label = ctk.CTkLabel(ai_instructions_frame, text=instructions_text, 
                                        font=ctk.CTkFont(size=11), justify="left")
        instructions_label.pack(anchor="w", padx=10, pady=5)


    def show_raw_response_popup(self, raw_text, title="RAW RESPONSE"):
        popup = tk.Toplevel(self.root)
        popup.title(title)  # ใช้ title แบบกำหนดเอง
        text_widget = tk.Text(popup, wrap="word")
        text_widget.insert("1.0", raw_text)
        text_widget.pack(expand=True, fill="both")
        text_widget.config(state="normal")

        def copy_to_clipboard():
            self.root.clipboard_clear()
            self.root.clipboard_append(raw_text)
        copy_btn = tk.Button(popup, text="Copy to Clipboard", command=copy_to_clipboard)
        copy_btn.pack(pady=5)


    def update_progress(self, progress, message):
        """Update the progress bar and message"""
        self.progress_bar.set(progress)
        self.status_label.configure(text=message)

    def normalize_connections(self, connections):
        """Ensure all connections are in the correct n8n format (main: [[]])"""
        if not isinstance(connections, dict):
            return {}
        fixed = {}
        for node, conn in connections.items():
            fixed[node] = {}
            for key, val in conn.items():
                # ต้องเป็น list of list
                if isinstance(val, list):
                    if len(val) == 0 or not isinstance(val[0], list):
                        fixed[node][key] = [val]
                    else:
                        fixed[node][key] = val
                else:
                    # ถ้าเป็น dict หรืออื่นๆ ให้ wrap เป็น list of list
                    fixed[node][key] = [[val]]
            # ถ้าไม่มี connection เลย ให้เป็น [[]]
            if not fixed[node]:
                fixed[node]["main"] = [[]]
        return fixed


        
    def display_workflow(self, workflow):
        json_str = json.dumps(workflow, indent=2, ensure_ascii=False)
        self.json_output.configure(state="normal")
        self.json_output.delete("1.0", "end")
        self.log_message(f"✅ Workflow generated with {len(workflow.get('nodes', []))} nodes: {[n.get('name', n.get('id', 'no-name')) for n in workflow.get('nodes', [])]}")
        self.json_output.insert("1.0", json_str)
        self.json_output.configure(state="normal")  # ให้ copy ได้
        self.notebook.set("📄 Output")
        self.download_btn.configure(state="normal")
        self.upload_n8n_btn.configure(state="normal", text="⬆️ Upload to n8n")
        
    def explain_workflow(self, workflow):
        if not workflow or "nodes" not in workflow:
            return
        prompt = (
            "Analyze the following n8n workflow JSON and explain in Thai what this workflow does, "
            "what is the main system or process, and how it works step by step. "
            "Do not just list node names. Summarize the purpose and flow for a non-technical user.\n\n"
            "Workflow JSON:\n"
            f"{json.dumps(workflow, ensure_ascii=False, indent=2)}"
        )
        try:
            payload = {
                "model": "deepseek-coder:6.7b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 1024}
            }
            response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=300)
            ai_explanation = response.json().get("response", "").strip()
            if not ai_explanation:
                ai_explanation = "ไม่สามารถวิเคราะห์ workflow ได้"
        except Exception as e:
            ai_explanation = f"❌ วิเคราะห์ workflow ไม่สำเร็จ: {str(e)}"
        messagebox.showinfo("AI วิเคราะห์ Workflow", ai_explanation)
    def download_workflow(self):
        """Download the generated workflow as JSON file"""
        if not self.generated_workflow:
            messagebox.showerror("Error", "No workflow to download.")
            return
        
        filename = filedialog.asksaveasfilename(
            title="Save Workflow JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                # Save the generated workflow to the chosen file
                workflow = self.fix_workflow_for_n8n(self.generated_workflow)
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(self.generated_workflow, f, indent=2, ensure_ascii=False)
                
                # Inform the user that the workflow has been saved
                messagebox.showinfo("Success", f"Workflow saved to: {filename}")
                self.log_message(f"✅ Workflow downloaded: {filename}")
                
            except Exception as e:
                # Handle errors while saving the file
                messagebox.showerror("Error", f"Failed to save file: {str(e)}")
                self.log_message(f"❌ Download failed: {str(e)}")

    
    
    def setup_generate_tab(self):
        tab = self.tab_generate

        # Title
        title_label = ctk.CTkLabel(tab, text="Generate Workflow from Class Diagram", font=ctk.CTkFont(size=16, weight="bold"))
        title_label.pack(pady=(20, 10))

        # Workflow Name
        workflow_name_label = ctk.CTkLabel(tab, text="Workflow Name:")
        workflow_name_label.pack(anchor="w", padx=20)
        self.workflow_name_entry = ctk.CTkEntry(tab)
        self.workflow_name_entry.pack(fill="x", padx=20, pady=(0, 10))
        self.diagram_description_label = ctk.CTkLabel(tab, text="Diagram Description (optional):")
        self.diagram_description_label.pack(anchor="w", padx=20, pady=(10, 0))
        self.diagram_description_entry = ctk.CTkTextbox(self.tab_generate, width=400, height=100)
        self.diagram_description_entry.pack(pady=10)

        # ✅ Diagram Description (NEW)

        if hasattr(self, 'diagram_description_entry'):
            user_description = self.diagram_description_entry.get("1.0", "end").strip()
        else:
            user_description = ""

        self.diagram_description_entry.pack(fill="x", padx=20, pady=(0, 20))
        self.diagram_description_entry.insert("1.0", "e.g")

        # Generate Button
        self.generate_button = ctk.CTkButton(tab, text="Generate Workflow", command=self.on_generate_workflow_click)
        self.generate_button.pack(pady=(10, 5))

    
        # Progress Bar
        self.progress_bar = ctk.CTkProgressBar(tab)
        self.progress_bar.pack(fill="x", padx=20, pady=(0, 10))
        self.progress_bar.set(0)

        # Status Label
        self.status_label = ctk.CTkLabel(tab, text="", text_color="gray")
        self.status_label.pack(pady=(0, 10))


    def upload_workflow_to_n8n(self):
        if not hasattr(self, "generated_workflow") or not self.generated_workflow:
            self.log_message("⚠️ No workflow to upload.")
            return

        api_key = None
        if hasattr(self, "n8n_api_key_entry"):
            api_key = self.n8n_api_key_entry.get().strip()
        if not api_key:
            api_key = os.getenv("N8N_API_KEY") or getattr(self, "n8n_api_key", None)
        base_url = None
        if hasattr(self, "n8n_url_entry"):
            base_url = self.n8n_url_entry.get().strip()
        if not base_url:
            base_url = os.getenv("N8N_BASE_URL") or getattr(self, "n8n_url", "http://localhost:5678")
        if not api_key:
            self.log_message("❌ No API key found. Please set N8N_API_KEY in .env or UI.")
            return

        headers = {
            "Content-Type": "application/json",
            "X-N8N-API-KEY": api_key
        }
        endpoint = f"{base_url.rstrip('/')}/api/v1/workflows"

        # Always fix workflow before upload
        workflow = self.fix_workflow_for_n8n(self.generated_workflow)

        # ลบ property ที่ n8n API ไม่รองรับ
        allowed_keys = {"name", "nodes", "connections", "settings"}
        workflow = {k: v for k, v in workflow.items() if k in allowed_keys}
        if "settings" not in workflow or not isinstance(workflow["settings"], dict):
            workflow["settings"] = {}

        try:
            self.log_message("🔎 Workflow preview (JSON):")
            preview = json.dumps(workflow, indent=2, ensure_ascii=False)
            print(preview)
            self.log_message(preview[:500])

            self.log_message("📤 Uploading workflow to n8n...")

            response = requests.post(endpoint, headers=headers, json=workflow)
            if not response.ok:
                raise Exception(f"❌ Upload failed: {response.status_code} {response.text}")

            result = response.json()
            workflow_id = result.get("id")

            self.log_message(f"✅ Uploaded successfully! Workflow ID: {workflow_id}")


        except Exception as e:
            self.log_message(f"❌ Upload failed: {str(e)}")
        
    def start_generation(self):
        self.generate_button.configure(state="disabled")
        def run():
            try:
                if not self.available_models:
                    self.log_message("⚠️ No available models found. Retrying Ollama connection...")
                    self.check_ollama_connection()
                    time.sleep(2)  # รอให้เช็ค connection เสร็จ
                    if not self.available_models:
                        self.progress_bar.set(0)
                        messagebox.showerror("Error", "No available models found. Please check your Ollama connection and models.")
                        self.log_message("❌ Generation failed: No available models.")
                        return
            # ...rest of function...

                # 1. OCR
                ocr_model = next((m for m in self.available_models if "llava" in m or "bark-ocr" in m), None)
                if not ocr_model:
                    ocr_model = "llava:13b" if "llava:13b" in self.available_models else self.available_models[0]
                plantuml_text = self.analyze_image_with_llava(self.uploaded_image_path, model_name=ocr_model)

                # 2. Logic
                logic_model = next((m for m in self.available_models if "phi3" in m or "deepseek" in m or "llama3" in m or "mistral" in m), None)
                if not logic_model:
                    logic_model = self.available_models[0]
                logic_text = self.refine_logic_with_phi3(plantuml_text, model_name=logic_model)
                if not logic_text or "no text could be extracted" in logic_text.lower():
                    self.log_message("❌ Logic extraction failed or empty. Please check your diagram.")
                    messagebox.showerror("Error", "Logic extraction failed or empty. Please check your diagram.")
                    self.generate_button.configure(state="normal")
                    return

                # 3. Workflow (user เลือกเอง)
                code_model = self.model_dropdown.get()
                # ตัด logic_text ถ้ายาวเกินไป
                logic_text_short = logic_text[:2000]
                workflow_json_str = self.generate_n8n_json_with_deepseek(logic_text_short, model_name=code_model)
                self.log_message(f"Prompt to LLM:\n{workflow_json_str[:1000]}...")

                workflow_json = self.extract_json_from_response(workflow_json_str)
                if not isinstance(workflow_json, dict) or "nodes" not in workflow_json or "connections" not in workflow_json:
                    self.log_message("⚠️ LLM did not return valid JSON, trying fallback model...")
                    if code_model != "deepseek-coder:6.7b" and "deepseek-coder:6.7b" in self.available_models:
                        workflow_json_str = self.generate_n8n_json_with_deepseek(logic_text_short, model_name="deepseek-coder:6.7b")
                        workflow_json = self.extract_json_from_response(workflow_json_str)
                    if not isinstance(workflow_json, dict) or "nodes" not in workflow_json or "connections" not in workflow_json:
                        self.log_message("❌ All models failed. Please try a simpler diagram or another model.")
                        workflow_json = self.create_fallback_n8n_workflow({}, "fallback")
                        workflow = self.postprocess_node_types(workflow)

                # 4. Validate workflow JSON
                if "settings" not in workflow_json or not isinstance(workflow_json["settings"], dict):
                    workflow_json["settings"] = {}
                if "name" not in workflow_json or not workflow_json["name"]:
                    workflow_json["name"] = "Auto Generated Workflow"
                if "active" not in workflow_json:
                    workflow_json["active"] = False

                self.generated_workflow = workflow_json
                self.display_workflow(workflow_json)
                self.upload_n8n_btn.configure(state="normal")
                self.download_btn.configure(state="normal")
            except Exception as e:
                self.progress_bar.set(0)
                self.status_label.configure(text="❌ Generation failed")
                self.log_message(f"❌ Generation failed: {str(e)}")
                messagebox.showerror("Error", f"Generation failed: {str(e)}")
            finally:
                self.generate_button.configure(state="normal")
        threading.Thread(target=run, daemon=True).start()
        
    def setup_output_tab(self):
        self.output_tab = self.notebook.add("📄 Output")

        self.json_output = ctk.CTkTextbox(self.output_tab, state="normal", wrap="none", height=400)
        self.json_output.pack(padx=10, pady=10, fill="both", expand=True)

        btn_row = ctk.CTkFrame(self.output_tab)
        btn_row.pack(pady=10)

        self.copy_button = ctk.CTkButton(btn_row, text="📋 Copy", command=self.copy_json_output)
        self.copy_button.pack(side="left", padx=10)

        self.download_button = ctk.CTkButton(btn_row, text="💾 Download", command=self.download_json, state="disabled")
        self.download_button.pack(side="left", padx=10)

        self.upload_button = ctk.CTkButton(
            btn_row,
            text="⬆ Upload to n8n",
            command=self.upload_workflow_to_n8n,
            state="disabled"
        )
        self.upload_button.pack(side="left", padx=10)

        # เพิ่มปุ่ม AI แนะนำการใช้งาน workflow
        self.ai_explain_btn = ctk.CTkButton(
            btn_row,
            text="🤖 AI แนะนำการใช้งาน Workflow",
            command=self.show_ai_workflow_explanation,
            state="normal"
        )
        self.ai_explain_btn.pack(side="left", padx=10)


    def show_ai_workflow_explanation(self):
        """เรียก AI อธิบายการใช้งาน workflow พร้อมแสดงใน popup ที่ scroll ได้"""
        if not self.generated_workflow:
            messagebox.showinfo("AI แนะนำ", "ยังไม่มี workflow กรุณาสร้าง workflow ก่อน")
            return

        workflow = self.generated_workflow
        nodes = workflow.get("nodes", [])
        connections = workflow.get("connections", {})

        # สร้างสรุป node และลำดับการเชื่อมต่อ
        node_names = [node.get("name", node.get("id", "")) for node in nodes]
        node_types = [node.get("type", "") for node in nodes]
        connection_order = []
        for from_node, conn in connections.items():
            for branch in conn.get("main", []):
                for target in branch:
                    connection_order.append(f"{from_node} → {target.get('node', '')}")

        # สร้าง prompt ให้ AI วิเคราะห์แบบละเอียด
        prompt = (
            "Analyze the following n8n workflow JSON and explain in Thai:\n"
            "- What nodes are created and their types\n"
            "- The order of connections between nodes\n"
            "- What kind of system or process this workflow builds\n"
            "- What are the benefits and how to use it step by step\n"
            "Summarize for a non-technical user. If the explanation is long, break into sections.\n\n"
            "Node list:\n"
            + "\n".join([f"{i+1}. {name} ({typ})" for i, (name, typ) in enumerate(zip(node_names, node_types))]) +
            "\n\nConnection order:\n"
            + "\n".join(connection_order) +
            "\n\nWorkflow JSON:\n"
            + json.dumps(workflow, ensure_ascii=False, indent=2)
        )

        try:
            payload = {
                "model": "deepseek-coder:6.7b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 1024}
            }
            response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=300)
            ai_explanation = response.json().get("response", "").strip()
            if not ai_explanation:
                ai_explanation = "ไม่สามารถวิเคราะห์ workflow ได้"
        except Exception as e:
            ai_explanation = f"❌ วิเคราะห์ workflow ไม่สำเร็จ: {str(e)}"

        # แสดง popup ที่ scroll ได้
        popup = tk.Toplevel(self.root)
        popup.title("AI แนะนำการใช้งาน Workflow")
        popup.geometry("800x600")
        frame = tk.Frame(popup)
        frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        text_widget = tk.Text(frame, wrap="word", yscrollcommand=scrollbar.set)
        text_widget.insert("1.0", ai_explanation)
        text_widget.pack(fill="both", expand=True)
        scrollbar.config(command=text_widget.yview)
        text_widget.config(state="normal")

        def copy_to_clipboard():
            self.root.clipboard_clear()
            self.root.clipboard_append(ai_explanation)
        copy_btn = tk.Button(popup, text="Copy to Clipboard", command=copy_to_clipboard)
        copy_btn.pack(pady=5)




    def download_json(self):
        preview = json.dumps(self.generated_workflow, indent=2)
        self.log_message(f"DEBUG: Export preview (first 300 chars): {preview[:300]}")

        if not self.generated_workflow:
            self.log_message("⚠️ No workflow to download.")
            return

        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if not file_path:
            self.log_message("❌ Download canceled.")
            return

        try:
            workflow = self.fix_workflow_for_n8n(self.generated_workflow)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.generated_workflow, f, indent=2)
            self.log_message(f"✅ Workflow saved to: {file_path}")
        except Exception as e:
            self.log_message(f"❌ Failed to save JSON: {str(e)}")

    def copy_json(self):
        if self.generated_workflow:
            json_str = json.dumps(self.generated_workflow, indent=2)
            self.root.clipboard_clear()
            self.root.clipboard_append(json_str)
            self.root.update()  # รักษา clipboard
            self.log_message("✅ Copied JSON to clipboard.")
        else:
            self.log_message("⚠️ No workflow to copy.")

if __name__ == "__main__":
    app = DiagramToN8nApp()
    app.root.mainloop()
