# Works on Hugging Face Spaces (Docker), Railway, Fly.io, Cloud Run... The platform sets PORT.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=7860 \
    PYTHONUNBUFFERED=1 \
    FARMHAND_MODEL=gemini-3.1-flash-lite,gemini-3.5-flash-lite \
    FARMHAND_VERIFIER_MODEL=gemini-3.5-flash-lite,gemini-3.1-flash-lite \
    LLM_THINKING=low
EXPOSE 7860
CMD ["python", "run.py"]
