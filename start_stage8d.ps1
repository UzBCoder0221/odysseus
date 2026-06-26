$env:AUTH_ENABLED="false"
Set-Location "C:\Users\user\Desktop\sds\odysseus"
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --log-level warning
