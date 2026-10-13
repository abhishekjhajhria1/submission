# Works on Hugging Face Spaces (Docker), Railway, Fly.io, Cloud Run... The platform sets PORT.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=7860
ENV PYTHONUNBUFFERED=1
ENV FARMHAND_MODEL=gemini-3.1-flash-lite,gemini-3.5-flash-lite
ENV FARMHAND_VERIFIER_MODEL=gemini-3.5-flash-lite,gemini-3.1-flash-lite
ENV LLM_THINKING=low
ENV FARMHAND_MAX_RUNS_PER_DAY=25
EXPOSE 7860
CMD ["python", "run.py"]
