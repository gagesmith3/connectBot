#!/bin/bash
# Connect Bot Setup Script for Raspberry Pi
# Usage: bash setup.sh

set -e

echo "🤖 Connect Bot Setup for Raspberry Pi"
echo "======================================"
echo ""

# Check Python version
python3 --version
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if [[ "$PYTHON_VERSION" < "3.8" ]]; then
    echo "❌ Python 3.8+ required (found $PYTHON_VERSION)"
    exit 1
fi
echo "✅ Python $PYTHON_VERSION"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo ""
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment exists"
fi

# Activate venv
source venv/bin/activate
echo "✅ Virtual environment activated"

# Install dependencies
echo ""
echo "📖 Installing dependencies..."
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
echo "✅ Dependencies installed"

# Check if .env exists
echo ""
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "📝 Created .env from .env.example"
        echo "⚠️  IMPORTANT: Edit .env with your credentials:"
        echo "     - SLACK_BOT_TOKEN"
        echo "     - SLACK_SIGNING_SECRET"
        echo "     - OPENAI_API_KEY"
        echo "     - FASTAPI_API_KEY"
        echo ""
    fi
else
    echo "✅ .env already exists"
fi

# Create logs directory
if [ ! -d "logs" ]; then
    mkdir -p logs
    echo "✅ Created logs directory"
fi

echo ""
echo "======================================"
echo "✨ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env with your configuration:"
echo "   nano .env"
echo ""
echo "2. Run the bot:"
echo "   python main.py"
echo ""
echo "3. For systemd service:"
echo "   sudo cp connectbot.service /etc/systemd/system/"
echo "   sudo systemctl enable connectbot"
echo "   sudo systemctl start connectbot"
echo ""
