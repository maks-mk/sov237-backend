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
            # Можно задать envelope-from отдельно, если внедрите VERP
            server.sendmail(smtp_user, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            if use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())


def send_email_to_owner(*, name: str, email: str, message: str) -> None:
    """
    Письмо владельцу сайта с содержимым формы.
    """
    _smtp_config()  # проверяем наличие настроек

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Новое сообщение с формы обратной связи'
    msg['From'] = os.getenv('SMTP_USER', 'no-reply@example.com')
    msg['To'] = RECIPIENT_EMAIL
    msg['Reply-To'] = email

    # Простой, нейтральный HTML без переменных из другого контекста
    html_body = f"""
<!doctype html>
<html>
  <body style="font-family:Arial,Helvetica,sans-serif">
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
    Автоматический ответ с красивым HTML-шаблоном для проекта по реконструкции дорог.
    Настраиваемые поля по-прежнему читаются из переменных окружения (при желании):
      ACK_SUBJECT, ACK_GREETING, ACK_SIGNATURE
    """
    _smtp_config()  # проверяем, что SMTP настроен

    subject = os.getenv('ACK_SUBJECT', 'Спасибо за поддержку проекта по реконструкции дорог!')
    greeting = os.getenv('ACK_GREETING', 'Здравствуйте')
    signature = os.getenv('ACK_SIGNATURE', 'С уважением, команда проекта «Дороги Шахты»')

    msg = MIMEMultipart('alternative')  # при добавлении CID-картинок можно заменить на 'related'
    msg['Subject'] = subject
    msg['From'] = os.getenv('SMTP_USER', 'no-reply@example.com')
    msg['To'] = user_email
    msg['Reply-To'] = RECIPIENT_EMAIL

    text_body = (
        f"{greeting}, {name}!\n\n"
        "Мы получили ваше сообщение и обязательно его рассмотрим.\n"
        "Спасибо за поддержку проекта по реконструкции дорог в нашем районе.\n\n"
        "Ваше сообщение:\n"
        f"{original_message}\n\n"
        f"{signature}\n"
    )

    html_body = f"""
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#0b1220;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b1220;padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#0f172a;border-radius:16px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;">
            <tr>
              <td style="padding:28px 28px 0; text-align:center;">
                <div style="display:inline-block;padding:6px 12px;border-radius:999px;background:#0b1220;border:1px solid #1f2937;color:#94a3b8;font-size:12px;">Проект «Дороги Шахты»</div>
                <h2 style="margin:12px 0 4px; color:#dbeafe; font-size:24px; line-height:1.3;">Спасибо за поддержку!</h2>
                <div style="height:2px;background:linear-gradient(90deg,#60a5fa,#8b5cf6); margin:12px auto 0; width:160px;"></div>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 28px 8px;">
                <p style="margin:0 0 12px; color:#e2e8f0; font-size:16px;">{greeting}, {name}!</p>
                <p style="margin:0 0 12px; color:#cbd5e1; font-size:15px; line-height:1.6;">
                  Мы получили ваше сообщение и обязательно его рассмотрим.
                  Спасибо, что поддерживаете проект по реконструкции внутриквартальных дорог в нашем районе —
                  ваша активность помогает улучшать инфраструктуру.
                </p>
                <div style="background:#0b1220;border:1px solid #1f2937;border-radius:12px;padding:16px;margin:18px 0;">
                  <div style="color:#94a3b8;font-size:12px;margin-bottom:8px;">Ваше сообщение</div>
                  <div style="white-space:pre-wrap;color:#e5e7eb;font-size:14px;line-height:1.6;">{original_message}</div>
                </div>
                <p style="margin:18px 0;">
                  <a href="https://maks-mk.github.io/sov237" style="background:linear-gradient(90deg,#4f46e5,#9333ea);color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:10px;display:inline-block;font-size:14px;">
                    Узнать о проекта
                  </a>
                </p>
                <p style="margin:16px 0 0; color:#94a3b8; font-size:13px;">
                  {signature}
                </p>
              </td>
            </tr>
            <tr>
              <td style="background:#0b1220;color:#64748b;font-size:12px;padding:14px 28px;text-align:center;border-top:1px solid #1f2937;">
                Если письмо попало в спам — добавьте нас в адресную книгу.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()

    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    _smtp_send(msg, [user_email])


app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=True)
