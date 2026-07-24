import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.app_modules.base.config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SMTP_FROM_EMAIL,
    FRONTEND_URL,
)

logger = logging.getLogger("vtt.email")


async def send_verification_email(to_email: str, username: str, token: str) -> bool:
    """Invia un'email di verifica all'utente o la registra nella console se SMTP non è configurato."""
    verification_url = f"{FRONTEND_URL}/verify-email?token={token}"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #050508; color: #e2e8f0; margin: 0; padding: 40px 20px; }}
            .container {{ max-width: 560px; margin: 0 auto; background: #0f0f17; border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 20px; padding: 32px; text-align: center; box-shadow: 0 20px 50px rgba(0,0,0,0.6); }}
            h1 {{ color: #ffffff; margin-bottom: 8px; font-size: 26px; }}
            p {{ color: #94a3b8; font-size: 15px; line-height: 1.6; margin-bottom: 24px; }}
            .btn {{ display: inline-block; background: linear-gradient(135deg, #a855f7, #6366f1); color: #ffffff !important; font-weight: bold; padding: 14px 32px; border-radius: 50px; text-decoration: none; font-size: 16px; margin: 16px 0; box-shadow: 0 0 20px rgba(168,85,247,0.4); }}
            .footer {{ margin-top: 32px; font-size: 12px; color: #64748b; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 16px; }}
            .url {{ word-break: break-all; color: #a855f7; font-size: 12px; font-family: monospace; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1> Benvenuto su WizVTT, {username}!</h1>
            <p>Grazie per esserti registrato. Per completare la creazione del tuo account e iniziare a giocare, conferma il tuo indirizzo email cliccando sul pulsante sottostante:</p>
            <a href="{verification_url}" class="btn">Conferma la mia Email</a>
            <p>Se il pulsante non funziona, copia e incolla questo link nel tuo browser:</p>
            <p class="url">{verification_url}</p>
            <div class="footer">
                <p>Se non hai richiesto tu questa registrazione, puoi ignorare questa email.</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Se SMTP non è configurato, logghiamo il link in console (Dev mode)
    if not SMTP_HOST or not SMTP_USER:
        print("=================================================================", flush=True)
        print("📧 [EMAIL TRANSAZIONALE - MODALITÀ DEV / LOG CONSOLE]", flush=True)
        print(f"A: {to_email} (Utente: {username})", flush=True)
        print(f"Link di conferma: {verification_url}", flush=True)
        print("=================================================================", flush=True)
        logger.info(f"📧 [EMAIL DEV] A: {to_email} | Link: {verification_url}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Conferma il tuo indirizzo email - WizVTT"
        msg["From"] = SMTP_FROM_EMAIL
        msg["To"] = to_email

        part = MIMEText(html_content, "html")
        msg.attach(part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())

        logger.info(f"Email di verifica inviata a {to_email}")
        return True
    except Exception as e:
        logger.error(f"Errore durante l'invio dell'email a {to_email}: {e}", exc_info=True)
        return False
