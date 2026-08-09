# Klinik köprü (FastAPI). Postgres + OpenWA ayrı servislerde (docker-compose.yml).
FROM python:3.13-slim
# date.today() / hafta_basi UTC değil Europe/Istanbul'a göre çalışsın.
# Coolify env'inde TZ olmasa bile container doğru saat diliminde kalsın;
# yoksa panel/demo bu hafta yerine geçen haftayı açar.
ENV TZ=Europe/Istanbul
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
