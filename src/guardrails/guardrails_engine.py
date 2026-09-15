import os
import re
import shlex
import bashlex

# ---------------------------------------------------------------------------
# RISK CONSTANTS
# ---------------------------------------------------------------------------
RISK_LEVELS = {
    "VERY LOW": 1,
    "LOW": 2,
    "MEDIUM": 3,
    "HIGH": 4,
    "VERY HIGH": 5
}

# ---------------------------------------------------------------------------
# LAYER 2: WHITELISTS & POLICY REGISTRIES
# ---------------------------------------------------------------------------
KNOWN_READ_ONLY = {
    "uptime", "free", "whoami", "date", "cat", "ls", "pwd",
    "grep", "tail", "head", "jq", "ps", "awk", "hostname", "uname", "wc"
}

KNOWN_INSPECTION = {
    "df", "du", "docker", "systemctl", "ip", "ss", "netstat", "find", "journalctl", "dmesg"
}

KNOWN_MUTATORS = {
    "apt", "apt-get", "yum", "dnf", "cp", "mv", "touch", "mkdir",
    "sed", "ufw", "iptables", "rsync", "chmod", "chown", "nano", "vim"
}

KNOWN_DESTRUCTIVE = {
    "rm", "dd", "mkfs", "fdisk", "parted", "userdel", "groupdel",
    "reboot", "shutdown", "poweroff", "kill", "pkill", "systemctl"
}

BANNED_SHELL_BUILTINS = {
    "eval", "exec", "source", ".", "alias", "unalias", "builtin"
}

INTERPRETER_BINARIES = {
    "python", "python3", "perl", "ruby", "php", "node", "bash", "sh", "zsh"
}

# ---------------------------------------------------------------------------
# LAYER 3: APPROVED EXECUTABLE PATHS
# ---------------------------------------------------------------------------
APPROVED_SYSTEM_PATHS = {
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
    "/usr/local/bin",
    "/opt/homie/approved_scripts"  # Admin-approved custom folder
}


class GuardrailEngine:
    def __init__(self, raw_command: str, llm_reported_risk: str = "VERY LOW"):
        self.raw_command = raw_command.strip()
        self.llm_risk_str = llm_reported_risk.upper()
        self.llm_risk_val = RISK_LEVELS.get(self.llm_risk_str, 5)

    def evaluate(self) -> dict:
        """Runs the multi-layered evaluation pipeline."""
        
        # --- PRE-CHECK: Hard Deny Regex (Immediate Reject) ---
        hard_deny = self._check_hard_denylist()
        if hard_deny:
            return {
                "allowed": False,
                "action": "BLOCKED",
                "reason": f"Hard Deny Rule Triggered: {hard_deny}",
                "effective_risk": "VERY HIGH"
            }

        # --- LAYER 1 & 2: Parse AST & Analyze Nodes ---
        try:
            ast_nodes = bashlex.parse(self.raw_command)
        except Exception as e:
            # If bashlex fails to parse the command (malformed/obfuscated syntax), lock it down.
            return {
                "allowed": False,
                "action": "BLOCKED",
                "reason": f"Syntax Parsing Error (Possible Obfuscation Attempt): {str(e)}",
                "effective_risk": "VERY HIGH"
            }

        max_calculated_risk = RISK_LEVELS["VERY LOW"]
        eval_reasons = []

        for node in ast_nodes:
            risk_score, reasons = self._visit_node(node)
            eval_reasons.extend(reasons)
            if risk_score > max_calculated_risk:
                max_calculated_risk = risk_score

        # --- LAYER 1: Anomaly Sentinel (LLM vs Calculated Risk) ---
        risk_delta = max_calculated_risk - self.llm_risk_val
        if risk_delta >= 2 and max_calculated_risk >= RISK_LEVELS["HIGH"]:
            return {
                "allowed": False,
                "action": "QUARANTINE_SESSION",
                "reason": (
                    f"SECURITY ANOMALY DETECTED: LLM reported '{self.llm_risk_str}' risk, "
                    f"but system evaluated risk level to {max_calculated_risk}. "
                    "Possible prompt injection or context drift."
                ),
                "effective_risk": "VERY HIGH"
            }

        # Final Effective Risk is the HIGHER of LLM or System calculation
        effective_val = max(self.llm_risk_val, max_calculated_risk)
        effective_str = [k for k, v in RISK_LEVELS.items() if v == effective_val][0]

        return {
            "allowed": True,
            "action": "PROCEED",
            "effective_risk": effective_str,
            "reasons": eval_reasons,
            "requires_human_approval": effective_val >= RISK_LEVELS["MEDIUM"]
        }

    def _check_hard_denylist(self) -> str | None:
        """Immediate regex check for unrecoverable destructive commands."""
        patterns = [
            (r"rm\s+(-[rRfF]+\s+)?/", "Recursive root/system path deletion attempt"),
            (r">\s*/dev/sd[a-z]", "Direct raw block device overwrite"),
            (r"mkfs(\.\w+)?\s+", "Filesystem formatting command"),
            (r"dd\s+if=", "Raw block copy/write operation"),
            (r"curl\s+.*\|\s*(bash|sh)", "Network pipeline to shell execution"),
            (r"chmod\s+(-R\s+)?777\s+/", "Global system permissions modification"),
            (r"iptables\s+-F", "Flushing main firewall tables")
        ]
        for pattern, reason in patterns:
            if re.search(pattern, self.raw_command):
                return reason
        return None

    def _visit_node(self, node) -> tuple[int, list[str]]:
        """Recursively traverses AST nodes to evaluate executables and flags."""
        node_risk = RISK_LEVELS["VERY LOW"]
        reasons = []

        if node.kind == "command":
            # Extract word tokens from command node
            words = [self.raw_command[w.pos[0]:w.pos[1]] for w in node.parts if w.kind == "word"]
            if not words:
                return node_risk, reasons

            raw_binary = words[0]
            args = words[1:]

            # ---------------------------------------------------------------
            # LAYER 1: Check Banned Shell Built-ins
            # ---------------------------------------------------------------
            if raw_binary in BANNED_SHELL_BUILTINS:
                return RISK_LEVELS["VERY HIGH"], [f"Banned shell built-in executed: '{raw_binary}'"]

            # ---------------------------------------------------------------
            # LAYER 3: PATH Boundary & Resolution Verification
            # ---------------------------------------------------------------
            binary_name = os.path.basename(raw_binary)
            
            # If command specifies a path (e.g. ./script.sh or /tmp/tool)
            if "/" in raw_binary:
                resolved_path = os.path.realpath(raw_binary)
                parent_dir = os.path.dirname(resolved_path)

                if parent_dir not in APPROVED_SYSTEM_PATHS:
                    return RISK_LEVELS["VERY HIGH"], [
                        f"PATH VIOLATION: Binary '{raw_binary}' resolves to non-approved directory '{parent_dir}'"
                    ]

            # ---------------------------------------------------------------
            # LAYER 1: Detect Inline Interpreter Abuses (e.g., python -c "...")
            # ---------------------------------------------------------------
            if binary_name in INTERPRETER_BINARIES:
                if any(flag in args for flag in ["-c", "-e", "--eval"]):
                    return RISK_LEVELS["VERY HIGH"], [
                        f"INLINE INTERPRETER DETECTED: '{binary_name}' with inline execution flags"
                    ]

            # ---------------------------------------------------------------
            # LAYER 2: Deterministic Classification & Strict Fallback
            # ---------------------------------------------------------------
            if binary_name in KNOWN_READ_ONLY:
                node_risk = max(node_risk, RISK_LEVELS["VERY LOW"])
                reasons.append(f"Known read-only command: '{binary_name}'")

            elif binary_name in KNOWN_INSPECTION:
                if binary_name == "docker" and any(a in args for a in ["run", "rm", "stop", "exec", "system"]):
                    node_risk = max(node_risk, RISK_LEVELS["MEDIUM"])
                    reasons.append("Docker state modification flag detected")
                elif binary_name == "systemctl" and any(a in args for a in ["stop", "disable"]):
                    node_risk = max(node_risk, RISK_LEVELS["HIGH"])
                    reasons.append("Systemctl service stopping/disabling flag detected")
                else:
                    node_risk = max(node_risk, RISK_LEVELS["LOW"])
                    reasons.append(f"Known inspection tool: '{binary_name}'")

            elif binary_name in KNOWN_MUTATORS:
                if binary_name in ["ufw", "iptables"]:
                    node_risk = max(node_risk, RISK_LEVELS["HIGH"])
                    reasons.append("Network rule modification command")
                else:
                    node_risk = max(node_risk, RISK_LEVELS["MEDIUM"])
                    reasons.append(f"Known state mutator command: '{binary_name}'")

            elif binary_name in KNOWN_DESTRUCTIVE:
                if binary_name in ["rm", "dd", "mkfs"]:
                    node_risk = max(node_risk, RISK_LEVELS["VERY HIGH"])
                else:
                    node_risk = max(node_risk, RISK_LEVELS["HIGH"])
                reasons.append(f"Known critical/destructive binary: '{binary_name}'")

            else:
                # -----------------------------------------------------------
                # DEFAULT-DENY FALLBACK: Unrecognized Binary
                # -----------------------------------------------------------
                node_risk = max(node_risk, RISK_LEVELS["VERY HIGH"])
                reasons.append(f"DEFAULT-DENY: Unrecognized binary/command '{binary_name}'")

        # Recurse down sub-trees (pipelines `|`, chainers `&&`, logical lists)
        if hasattr(node, "parts"):
            for child in node.parts:
                child_risk, child_reasons = self._visit_node(child)
                reasons.extend(child_reasons)
                if child_risk > node_risk:
                    node_risk = child_risk

        return node_risk, reasons