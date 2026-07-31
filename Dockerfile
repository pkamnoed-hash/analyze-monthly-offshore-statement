FROM python:3.12-slim

# Hugging Face Spaces runs Docker containers as a non-root user (uid 1000)
RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .
USER appuser

# Space builds default to port 7860; declared explicitly in README.md's app_port too
EXPOSE 7860

CMD ["streamlit", "run", "dashboard_app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
