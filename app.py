import os
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS


RECIPIENT_EMAIL = "sov23725@gmail.com"  # куда приходит сообщение с формы (админ/владелец)


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

        if not _looks_like_email(email):
            return jsonify({"error": "Некорректный email"}), 400

        try:
            # 1) Письмо владельцу сайта
            send_email_to_owner(name=name, email=email, message=message)
        except Exception as exc:
            return jsonify({"error": f"Не удалось отправить письмо владельцу: {exc}"}), 500

        # 2) Автоответ пользователю (не критично, если не получилось — не ломаем форму)
        try:
            send_thanks_email_to_user(name=name, user_email=email, original_message=message)
        except Exception as exc:
            # Можно записать в логи, но не валим запрос
            print(f"[WARN] Не удалось отправить автоответ пользователю: {exc}")

        return jsonify({"ok": True})

    @app.route('/health')
    def health_check():
        return "OK", 200

    return app


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _looks_like_email(addr: str) -> bool:
    # Простая проверка формата + защита от внедрения заголовков
    if "\n" in addr or "\r" in addr:
        return False
    return bool(EMAIL_RE.match(addr))


def _smtp_config():
    smtp_host = os.getenv('SMTP_HOST', '')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_pass = os.getenv('SMTP_PASS', '')
    use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes')
    use_ssl = os.getenv('SMTP_USE_SSL', 'false').lower() in ('1', 'true', 'yes')

    if not smtp_host or not smtp_user or not smtp_pass:
        raise RuntimeError('SMTP настройки не заданы (SMTP_HOST, SMTP_USER, SMTP_PASS)')

    return smtp_host, smtp_port, smtp_user, smtp_pass, use_tls, use_ssl


def _smtp_send(msg: MIMEMultipart, recipients: list[str]) -> None:
    smtp_host, smtp_port, smtp_user, smtp_pass, use_tls, use_ssl = _smtp_config()

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20, context=context) as server:
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(msg['From'], recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            if use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(msg['From'], recipients, msg.as_string())


def send_email_to_owner(*, name: str, email: str, message: str) -> None:
    """
    Письмо владельцу сайта с содержимым формы.
    """
    smtp_host, smtp_port, smtp_user, smtp_pass, use_tls, use_ssl = _smtp_config()

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

    _smtp_send(msg, [RECIPIENT_EMAIL])


def send_thanks_email_to_user(*, name: str, user_email: str, original_message: str) -> None:
    """
    Автоматический ответ на email пользователя из формы Поддержки.
    Текст можно кастомизировать через переменные окружения:
      ACK_SUBJECT — тема (по умолчанию 'Спасибо за поддержку!')
      ACK_GREETING — приветствие (по умолчанию 'Здравствуйте')
      ACK_SIGNATURE — подпись (по умолчанию 'С уважением, команда поддержки')
    """
    smtp_host, smtp_port, smtp_user, smtp_pass, use_tls, use_ssl = _smtp_config()

    subject = os.getenv('ACK_SUBJECT', 'Спасибо за поддержку проекта по реконструкции дорог!')
    greeting = os.getenv('ACK_GREETING', 'Здравствуйте')
    signature = os.getenv('ACK_SIGNATURE', 'С уважением, команда проекта по реконструкции дорог в нашем районе.')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = user_email
    msg['Reply-To'] = RECIPIENT_EMAIL

    html_body = f"""
    <html>
      <body>
        <p>{greeting}, {name}!</p>
        <p>Мы получили ваше сообщение и обязательно его рассмотрим.</p>
        <p>Спасибо, что поддерживаете проект по реконструкции дорог в нашем районе —
        ваша помощь и активное участие помогают нам улучшать инфраструктуру и благоустройство.</p>
        <hr/>
        <p><b>Ваше сообщение:</b><br/>{original_message.replace('\n', '<br/>')}</p>
        <br/>
        <p>{signature}</p>
      </body>
    </html>
    """.strip()

    text_body = (
        f"{greeting}, {name}!\n\n"
        "Мы получили ваше сообщение и посмотрим его в ближайшее время.\n"
        "Спасибо, что поддерживаете проект — это очень помогает нам развиваться.\n\n"
        "Ваше сообщение:\n"
        f"{original_message}\n\n"
        f"{signature}\n"
    )

    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    _smtp_send(msg, [user_email])


app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=True)
