import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS


RECIPIENT_EMAIL = "sov23725@gmail.com"


def create_app() -> Flask:
    app = Flask(__name__, static_folder='.', static_url_path='')
    CORS(app, origins=[
        "https://maks-mk.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "null"
    ])

    @app.route('/ping', methods=["GET", "HEAD"])
    def ping():
        return jsonify({"status": "ok"}), 200

    @app.route('/')
    def root():
        return send_from_directory('.', 'index.html')

    @app.route('/api/contact', methods=['POST'])
    def contact():
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        message = (data.get('message') or '').strip()

        if not name or not email or not message:
            return jsonify({"error": "Заполните все поля формы"}), 400

        try:
            send_email(name=name, email=email, message=message)
        except Exception as exc:
            return jsonify({"error": f"Не удалось отправить письмо: {exc}"}), 500

        return jsonify({"ok": True})
        
    @app.route('/health')
    def health_check():
        return "OK", 200

    return app


def send_email(*, name: str, email: str, message: str) -> None:
    smtp_host = os.getenv('SMTP_HOST', '')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_pass = os.getenv('SMTP_PASS', '')
    use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes')
    use_ssl = os.getenv('SMTP_USE_SSL', 'false').lower() in ('1', 'true', 'yes')

    if not smtp_host or not smtp_user or not smtp_pass:
        raise RuntimeError('SMTP настройки не заданы (SMTP_HOST, SMTP_USER, SMTP_PASS)')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Новое сообщение с формы обратной связи'
    msg['From'] = smtp_user
    msg['To'] = RECIPIENT_EMAIL
    msg['Reply-To'] = email

    html_body = f"""
    <html>
      <body>
        <h3>Новое сообщение</h3>
        <p><b>Имя:</b> {name}</p>
        <p><b>Email:</b> {email}</p>
        <p><b>Сообщение:</b><br/>{message.replace('\n', '<br/>')}</p>
      </body>
    </html>
    """.strip()

    text_body = (
        f"Новое сообщение\n\nИмя: {name}\nEmail: {email}\n\nСообщение:\n{message}\n"
    )

    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20, context=context) as server:
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [RECIPIENT_EMAIL], msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            if use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [RECIPIENT_EMAIL], msg.as_string())
            
app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=True)


