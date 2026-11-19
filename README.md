# Mirrorling

### A Voice-First Multimodal AI Companion

Mirrorling is a multimodal AI companion project that explores a more natural and personalised approach to human-AI interaction.

It combines conversational AI with facial-expression estimates, vocal characteristics and user-controlled persistent memory to support more context-aware companion interactions.

Rather than relying exclusively on what a user says, Mirrorling can also consider available signals from how they speak and their visible facial expressions.

**Project Goal:** To explore how multimodal perception, conversational AI and personalisation can make AI companionship more interactive and context-aware.

---

## Core Features

- **Multimodal Perception:** Combines conversational input with facial-expression estimates and vocal measurements.
- **AI Companions:** Four distinct companions, Moss, Nova, Echo and Zen, each with their own personality and voice.
- **Voice Interaction:** Speech recognition and AI-generated spoken responses.
- **Companion Designer:** Customise companion identity, personality, appearance and voice.
- **Persistent Memory:** MongoDB-backed memories with user-controlled permissions.
- **Adaptive Familiarity:** Supports gradual personalisation through meaningful information and recurring interactions.
- **Optional Camera Interaction:** Facial analysis can be enabled or disabled by the user.

## Technology Stack

| Component | Technologies |
|---|---|
| Frontend | React, Vite |
| Backend | Python, FastAPI |
| Database | MongoDB |
| Facial Tracking | MediaPipe Face Landmarker |
| Facial Expression Estimation | EmotiEffLib, ONNX |
| Speech Recognition | faster-whisper |
| Voice Analysis | PyAV, NumPy, WebRTC VAD, YIN |
| Language Model | Ollama, Llama 3.2 |
| Speech Generation | ElevenLabs, Piper fallback |

## System Architecture

Mirrorling follows a modular architecture in which perception and response components communicate through the backend.

1. **User Input:** The frontend receives typed messages or microphone recordings and can optionally access the camera.
2. **Speech Processing:** faster-whisper generates transcripts while the voice analysis module extracts acoustic measurements.
3. **Facial Processing:** MediaPipe tracks facial landmarks in the browser. Face crops are sent to the backend for EmotiEffLib inference.
4. **Backend Processing:** FastAPI receives available observations and conversational context, retrieves permitted memories and constructs a companion-aware prompt.
5. **Response Generation:** Ollama runs Llama 3.2 to generate the companion's response.
6. **Speech Output:** ElevenLabs generates spoken responses using companion-specific voices. Piper remains configured as a local fallback.

Camera and perception input are optional. Typed conversation does not require camera or microphone access.

## Project Structure

```text
Mirrorling/
|
|-- backend/
|   |-- main.py
|   |-- database.py
|   |-- emotion_service.py
|   |-- familiarity_service.py
|   |-- identity_routes.py
|   |-- identity_service.py
|   |-- llm_provider.py
|   |-- memory_models.py
|   |-- memory_retrieval.py
|   |-- memory_routes.py
|   |-- memory_service.py
|   |-- memory_store.py
|   |-- ollama_client.py
|   |-- personality.py
|   |-- provider_config.py
|   |-- speech_output.py
|   |-- speech_to_text.py
|   |-- voice_analysis.py
|   |-- requirements.txt
|   |-- pytest.ini
|   |-- test_*.py
|   `-- voices/
|
|-- frontend/
|   |-- public/
|   |   |-- mediapipe/
|   |   `-- models/
|   |-- src/
|   |   |-- App.jsx
|   |   |-- CameraPreview.jsx
|   |   |-- CompanionDesigner.jsx
|   |   |-- FaceAnalysis.jsx
|   |   |-- MemorySettings.jsx
|   |   `-- ...
|   |-- package.json
|   `-- vite.config.js
|
|-- start-dev.sh
|-- .gitignore
`-- README.md
```

## Getting Started

### Prerequisites

- Python 3.12
- Node.js LTS and npm
- MongoDB
- Ollama
- An ElevenLabs API key for the configured speech provider

### 1. Clone the Repository

```bash
git clone https://github.com/S-ShehryarBurney/Mirrorling.git
cd Mirrorling
```

### 2. Set Up the Backend

```bash
cd backend

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
```

Configure the required environment variables in `backend/.env`. Keep API keys private and never commit this file.

Ensure MongoDB is running and accessible using the database configuration expected by the backend.

### 3. Configure the Language Model

Install and start Ollama, then download the model:

```bash
ollama pull llama3.2:latest
```

The current project configuration uses Ollama as the active language model provider.

### 4. Start the Backend

From the `backend` directory:

```bash
uvicorn main:app --reload
```

Backend URL: http://127.0.0.1:8000

Interactive API documentation: http://127.0.0.1:8000/docs

### 5. Set Up and Start the Frontend

Open another terminal:

```bash
cd frontend

npm install

npm run dev -- --host 127.0.0.1
```

Open the development URL displayed by Vite.

## Testing

Run the backend regression tests:

```bash
cd backend
source .venv/bin/activate
pytest -q
```

Check Python syntax:

```bash
python -m py_compile *.py
```

Run frontend linting:

```bash
cd frontend
npm run lint
```

Build the frontend:

```bash
npm run build
```

The recorded validation run reported 111 backend tests passed and 6 skipped. Python compilation, frontend linting and the production build also completed successfully.

## Development Status

Mirrorling is a work-in-progress course project and functional prototype.

The current implementation includes:

- Companion-aware conversations.
- Facial and voice perception integration.
- Speech recognition and generation.
- Companion selection and customisation.
- MongoDB-backed user-controlled persistent memory.
- Relationship-specific memory permissions.

Further development is planned around perception accuracy, personalisation, accessibility, reliability and scalability.

Formal ground-truth evaluation of facial-expression and voice-prediction accuracy has not yet been completed. The current perception scores are model estimates, not measured prediction accuracy.

The Piper Kusal voice model is not included in this public repository and must be obtained separately to use the local Piper fallback. ElevenLabs is the active speech generation provider.

## Project Scope and Limitations

Mirrorling is designed as an AI companionship experience. It is not a therapist, medical diagnostic system or lie-detection tool.

Facial expressions and vocal characteristics are imperfect signals and should not be interpreted as definitive evidence of someone's internal emotional state.

The current project is a local development prototype rather than a production deployment. Production authentication, infrastructure hardening and comprehensive release safeguards remain future work.

---

*Exploring multimodal AI, computer vision, speech processing and personalised conversational systems.*