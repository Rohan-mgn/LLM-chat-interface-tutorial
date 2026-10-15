"""Private parser entry point. No models, uploaded-code execution or network tools."""
import json
from pathlib import Path
import sys

def main():
    request=json.loads(sys.stdin.buffer.read(16_384))
    from .parser_limits import worker_limits
    worker_limits()
    from .parser_registry import parse_local,MAX_OUTPUT
    try:
        sections=parse_local(Path(request["path"]),request["extension"])
        result={"sections":sections}
    except (ValueError,KeyError,UnicodeError) as error:
        result={"error":str(error)[:240]}
    except Exception:
        result={"error":"The document could not be parsed safely."}
    output=json.dumps(result,ensure_ascii=True).encode()
    if len(output)>MAX_OUTPUT:
        output=b'{"error":"Parsed representation exceeds its output limit."}'
    sys.stdout.buffer.write(output)

if __name__=="__main__":
    main()
