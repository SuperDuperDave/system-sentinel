import subprocess
import json
import logging
import base64
import datetime
import hashlib
import pathlib

logger = logging.getLogger(__name__)

def run_powershell(script: str, json_output: bool = True):
    """
    Executes a PowerShell script using -EncodedCommand.
    This safely passes complex scripts between WSL/Linux and Windows Host without quoting issues.
    """
    import tempfile
    import os

    # DEBUG: Track which script is running
    script_snippet = script[:100].replace('\n', ' ').strip()
    
    # Suppress Progress Stream to avoid CLIXML pollution on Stderr
    full_script = f"$ProgressPreference = 'SilentlyContinue'; {script}"

    encoded_script = base64.b64encode(full_script.encode('utf-16le')).decode('utf-8')
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_script]

    try:
        # Run from C:\ to avoid WSL path resolution issues that cause Exit Code 1
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
            cwd="C:\\" if os.path.exists("C:\\") else ("/mnt/c" if os.path.exists("/mnt/c") else None) 
        )
        
        stdout = result.stdout.strip()
        
        if result.returncode != 0:
            logger.warning(f"PowerShell exited with code {result.returncode}. Script: {script_snippet}... Stderr: {result.stderr[:500]}")
            return None

        if not stdout:
            logger.warning(f"PowerShell succeeded (Code 0) but returned empty STDOUT. Script: {script_snippet}... Stderr: {result.stderr[:200]}")
            return [] if json_output else ""

        if json_output:
            try:
                # Basic JSON extraction heuristic
                idx_arr = stdout.find('[')
                idx_obj = stdout.find('{')
                
                start_idx = -1
                if idx_arr != -1 and idx_obj != -1:
                    start_idx = min(idx_arr, idx_obj)
                elif idx_arr != -1:
                    start_idx = idx_arr
                elif idx_obj != -1:
                    start_idx = idx_obj
                
                if start_idx != -1:
                    clean_stdout = stdout[start_idx:]
                    return json.loads(clean_stdout)
                else:
                    return json.loads(stdout)

            except json.JSONDecodeError as e:
                err_msg = f"Failed to parse JSON output: {e}. Output snippet: {stdout[:2000]}"
                logger.error(err_msg)
                return None
        
        return stdout

    except Exception as e:
        logger.exception(f"Exception running PowerShell: {e}")
        return None
