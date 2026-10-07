"""
Test script to verify Connect Bot setup and connectivity
Usage: python test_setup.py
"""

import os
import sys
from pathlib import Path


def check_environment():
    """Verify all required environment variables are set"""
    print("Checking environment variables...")
    provider = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()

    required = {
        "SLACK_BOT_TOKEN": "xoxb-",
        "SLACK_SIGNING_SECRET": "At least 32 chars",
        "FASTAPI_API_KEY": "Your FastAPI key",
    }
    if provider == "ollama":
        required["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
        required["OLLAMA_MODEL"] = "gemma3:4b"
    else:
        required["OPENROUTER_API_KEY"] = "sk-or-v1-..."

    optional = {
        "FASTAPI_BASE_URL": "http://localhost:8100",
        "FASTAPI_TIMEOUT_SECONDS": "10",
        "FASTAPI_MAX_RETRIES": "2",
        "FASTAPI_RETRY_BACKOFF_SECONDS": "0.5",
        "OPENROUTER_MODELS": "Comma-separated OpenRouter model slugs",
        "LLM_PROVIDER": "openrouter",
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "OLLAMA_MODEL": "gemma3:4b",
        "USE_AGENT_MODE": "true",
        "AGENT_HISTORY_WINDOW": "6",
        "USE_SOCKET_MODE": "true",
    }

    all_found = True

    for key, example in required.items():
        value = os.getenv(key)
        if value:
            masked = value[:10] + "..." if len(value) > 10 else value
            print(f"  OK {key}: {masked}")
        else:
            print(f"  MISSING {key}: NOT SET (e.g., {example})")
            all_found = False

    print("\nOptional settings:")
    for key, default in optional.items():
        value = os.getenv(key, default)
        print(f"  INFO {key}: {value}")

    return all_found


def check_dependencies():
    """Verify all required packages are installed"""
    print("\nChecking dependencies...")
    required = [
        "slack_bolt",
        "slack_sdk",
        "httpx",
        "dotenv",
        "fastapi",
        "uvicorn",
    ]

    all_found = True
    for package in required:
        try:
            __import__(package)
            print(f"  OK {package}")
        except ImportError:
            print(f"  MISSING {package} (install via: pip install -r requirements.txt)")
            all_found = False

    return all_found


def check_directories():
    """Verify required directories exist"""
    print("\nChecking directories...")
    required = [
        "src/connectbot",
        "logs",
    ]

    for directory in required:
        if Path(directory).exists():
            print(f"  OK {directory}/")
        else:
            print(f"  MISSING {directory}/ (will be created on first run)")

    # Create logs directory
    Path("logs").mkdir(exist_ok=True)

    return True


def test_fastapi_connection():
    """Test connection to FastAPI server"""
    print("\nTesting FastAPI connection...")
    try:
        import httpx

        base_url = os.getenv("FASTAPI_BASE_URL", "http://localhost:8100")
        api_key = os.getenv("FASTAPI_API_KEY")

        if not api_key:
            print("  WARN FASTAPI_API_KEY not set - skipping connection test")
            return False

        client = httpx.Client(timeout=5.0)
        response = client.get(f"{base_url}/v1/metrics/backlog", headers={"X-API-Key": api_key})

        if response.status_code == 200:
            print(f"  OK Connected to {base_url}")
            data = response.json()
            print(f"     Got backlog data: {len(str(data))} chars")
            return True
        else:
            print(f"  FAIL FastAPI returned {response.status_code}")
            print(f"     Response: {response.text[:200]}")
            return False

    except Exception as e:
        print(f"  FAIL Error connecting to FastAPI: {e}")
        print("     Make sure FastAPI is running and FASTAPI_BASE_URL is correct")
        return False


def test_openrouter_connection():
    """Test connection to OpenRouter"""
    print("\nTesting OpenRouter connection...")
    try:
        import httpx

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            print("  WARN OPENROUTER_API_KEY not set - skipping OpenRouter test")
            return False

        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        response = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        model_count = len(payload.get("data", []))

        print("  OK Connected to OpenRouter API")
        print(f"     Available models: {model_count}")
        return True

    except Exception as e:
        print(f"  FAIL Error connecting to OpenRouter: {e}")
        return False


def test_ollama_connection():
    """Test connection to local Ollama runtime"""
    print("\nTesting Ollama connection...")
    try:
        import httpx

        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        model = os.getenv("OLLAMA_MODEL", "gemma3:4b")
        response = httpx.get(f"{base_url}/api/tags", timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        models = [item.get("name", "") for item in payload.get("models", [])]

        print(f"  OK Connected to Ollama at {base_url}")
        if model in models:
            print(f"  OK Model available: {model}")
            return True

        print(f"  WARN Ollama reachable, but model not found: {model}")
        print("     Pull it with: ollama pull " + model)
        return False
    except Exception as e:
        print(f"  FAIL Error connecting to Ollama: {e}")
        return False


def test_slack_config():
    """Test Slack configuration"""
    print("\nTesting Slack configuration...")

    from slack_bolt import App

    try:
        token = os.getenv("SLACK_BOT_TOKEN")
        secret = os.getenv("SLACK_SIGNING_SECRET")

        if not token or not secret:
            print("  FAIL SLACK_BOT_TOKEN or SLACK_SIGNING_SECRET not set")
            return False

        # Initialize but don't start
        App(token=token, signing_secret=secret)
        print("  OK Slack app configuration valid")
        return True

    except Exception as e:
        print(f"  FAIL Error with Slack config: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 50)
    print("Connect Bot - Setup Verification")
    print("=" * 50)

    # Load environment
    from dotenv import load_dotenv

    load_dotenv()

    provider = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()
    model_test_name = "Ollama" if provider == "ollama" else "OpenRouter"
    model_test_result = test_ollama_connection() if provider == "ollama" else test_openrouter_connection()

    results = {
        "Environment": check_environment(),
        "Dependencies": check_dependencies(),
        "Directories": check_directories(),
        "FastAPI": test_fastapi_connection(),
        model_test_name: model_test_result,
        "Slack": test_slack_config(),
    }

    print("\n" + "=" * 50)
    print("Test Results Summary")
    print("=" * 50)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  {test:<15}: {status}")

    print("\n" + "=" * 50)
    if passed == total:
        print(f"All tests passed! ({passed}/{total})")
        print("\nYou can now run: python main.py")
        return 0
    else:
        print(f"Some tests failed ({passed}/{total})")
        print("\nFix the issues above and run this script again.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
