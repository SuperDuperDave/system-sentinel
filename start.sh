#!/bin/bash
echo "Starting System Sentinel..."

# Function to kill subprocesses on exit
cleanup() {
    echo "Shutting down..."
    kill $(jobs -p)
}
trap cleanup EXIT

# Start Backend
echo "Starting Backend (FastAPI)..."
cd backend
if [ ! -d "venv" ]; then
    echo "Creating venv..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Start Frontend
echo "Starting Frontend (Next.js)..."
cd ../frontend
# Ensure dependencies are installed
if [ ! -f "node_modules/.bin/next" ]; then
    echo "Installing frontend dependencies..."
    npm install
    npm install lucide-react clsx tailwind-merge
fi
npm run dev &
FRONTEND_PID=$!

echo "System Sentinel is running!"
echo "Backend: http://localhost:8000"
echo "Frontend: http://localhost:3000"

wait $BACKEND_PID $FRONTEND_PID
