import os
import re
import json
import hashlib
import smtplib
import ssl
from datetime import datetime, timezone
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
        "http://192.168.0.3:8000",
        "http://192.168.0.3:8000/",
        "null"
    ])

    # === Конфигурация для голосования ===
    vote_file_path = os.getenv('VOTE_FILE_PATH', './vote.json')
    vote_salt = os.getenv('VOTE_SALT', 'demo_salt')

    @app.route('/ping', methods=["GET", "HEAD"])
    def ping():
        return jsonify({"status": "ok"}), 200

    @app.route('/')
    def root():
        return send_from_directory('.', 'index.html')

    @app.route('/api/votes', methods=['GET'])
    def get_votes():
        # Возвращаем текущие голоса из файла + опционально статус пользователя по fingerprint
        data = _load_votes(vote_file_path)
        stats = _stats_from_data(data)

        # Опционально проверим статус пользователя, если передан fingerprint
        fp_b64 = request.args.get('fingerprint', '').strip()
        user_info = {"hasVoted": False, "userVote": None}
        if fp_b64:
            ip = _get_client_ip(request)
            fp_hash = _hash_fingerprint(fp_b64, vote_salt)
            has_voted, rec = _has_user_voted(data, fp_hash)
            user_info["hasVoted"] = has_voted
            user_info["userVote"] = rec.get("vote") if rec else None

        return jsonify({
            **stats,
            **user_info
        })

    @app.route('/api/votes', methods=['POST'])
    def update_votes():
        # Простая заглушка для обновления голосов
        data = request.get_json(silent=True) or {}
        votes_for = data.get('votesFor', 245)
        votes_against = data.get('votesAgainst', 67)
        
        # В реальном приложении здесь была бы запись в БД
        return jsonify({
            "ok": True,
            "votesFor": votes_for,
            "votesAgainst": votes_against,
            "total": votes_for + votes_against
        })

    @app.route('/api/vote', methods=['POST'])
    def add_vote_route():
        payload = request.get_json(silent=True) or {}
        vote_type = (payload.get('vote') or '').strip().lower()
        fp_b64 = (payload.get('fingerprint') or '').strip()

        if vote_type not in ('for', 'against'):
            return jsonify({"error": "invalid_vote", "message": "Некорректное значение голоса"}), 400
        if not fp_b64 or len(fp_b64) > 4096:
            return jsonify({"error": "invalid_fingerprint", "message": "Некорректный отпечаток"}), 400

        data = _load_votes(vote_file_path)
        fp_hash = _hash_fingerprint(fp_b64, vote_salt)
        ip = _get_client_ip(request)
        ip_hash = _hash_ip(ip, vote_salt)

        already, rec = _has_user_voted(data, fp_hash)
        if already:
            stats = _stats_from_data(data)
            return jsonify({
                "error": "already_voted",
                "message": "Вы уже голосовали",
                "userVote": rec.get('vote') if rec else None,
                **stats
            }), 409

        # Добавляем голос
        try:
            data = _add_vote(data, fp_hash, vote_type, ip_hash)
            _save_votes(vote_file_path, data)
        except Exception:
            # Если проблемы с ФС — graceful degradation (не роняем)
            pass

        stats = _stats_from_data(data)
        return jsonify({
            "success": True,
            "message": "Голос учтен",
            **stats
        })

    @app.route('/api/vote/check', methods=['GET'])
    def check_vote_route():
        fp_b64 = request.args.get('fingerprint', '').strip()
        if not fp_b64:
            return jsonify({"error": "invalid_fingerprint", "message": "Отпечаток не задан"}), 400

        data = _load_votes(vote_file_path)
        fp_hash = _hash_fingerprint(fp_b64, vote_salt)
        has_voted, rec = _has_user_voted(data, fp_hash)
        if not has_voted:
            return jsonify({"hasVoted": False}), 200

        return jsonify({
            "hasVoted": True,
            "vote": rec.get('vote'),
            "timestamp": rec.get('timestamp')
        }), 200

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

        # Избыточное логирование PII убрано

        # Пытаемся отправить email, но не ломаем форму если не получается
        try:
            # Проверяем наличие SMTP настроек
            smtp_host = os.getenv('SMTP_HOST', '')
            if smtp_host:
                send_email_to_owner(name=name, email=email, message=message)
                send_thanks_email_to_user(name=name, user_email=email, original_message=message)
            else:
                pass  # SMTP не настроен
        except Exception as exc:
            print(f"[WARN] Ошибка отправки email: {exc}")
            import traceback
            traceback.print_exc()

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


# ======================= Голосование (helpers) =======================
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _load_votes(path: str) -> dict:
    """Загрузка данных из vote.json, если нет — создаем минимальную структуру."""
    try:
        if not os.path.exists(path):
            data = {
                "votes": {"for": 245, "against": 67},
                "voters": {},
                "metadata": {"total_votes": 312, "last_updated": _now_iso()}
            }
            _save_votes(path, data)
            return data
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        # Graceful degradation — вернем безопасные значения
        return {
            "votes": {"for": 245, "against": 67},
            "voters": {},
            "metadata": {"total_votes": 312, "last_updated": _now_iso()}
        }


def _save_votes(path: str, data: dict) -> None:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[WARN] Не удалось сохранить vote.json: {exc}")


def _hash_fingerprint(fingerprint_b64: str, salt: str) -> str:
    payload = (salt + '|' + fingerprint_b64).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _hash_ip(ip: str, salt: str) -> str:
    payload = (salt + '|ip|' + (ip or '')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _has_user_voted(data: dict, fingerprint_hash: str):
    rec = (data.get('voters') or {}).get(fingerprint_hash)
    return (rec is not None), (rec or {})


def _add_vote(data: dict, fingerprint_hash: str, vote_type: str, ip_hash: str) -> dict:
    votes = data.setdefault('votes', {"for": 0, "against": 0})
    voters = data.setdefault('voters', {})
    metadata = data.setdefault('metadata', {"total_votes": 0, "last_updated": _now_iso()})

    if fingerprint_hash in voters:
        return data  # уже голосовал

    if vote_type == 'for':
        votes['for'] = int(votes.get('for', 0)) + 1
    else:
        votes['against'] = int(votes.get('against', 0)) + 1

    voters[fingerprint_hash] = {
        "vote": vote_type,
        "timestamp": _now_iso(),
        "ip_hash": ip_hash
    }

    total = int(votes.get('for', 0)) + int(votes.get('against', 0))
    metadata['total_votes'] = total
    metadata['last_updated'] = _now_iso()
    return data


def _stats_from_data(data: dict) -> dict:
    votes = data.get('votes') or {}
    for_c = int(votes.get('for', 0))
    against_c = int(votes.get('against', 0))
    total = for_c + against_c
    return {"votesFor": for_c, "votesAgainst": against_c, "total": total}


def _get_client_ip(req) -> str:
    # Учитываем возможные прокси (например, Render)
    hdr = req.headers.get('X-Forwarded-For') or ''
    ip = (hdr.split(',')[0].strip() if hdr else req.remote_addr) or ''
    return ip


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
    
    # Убрано избыточное логирование SMTP-конфигурации

    try:
        if use_ssl:
            # Для SSL подключения (порт 465)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=context) as server:
                # server.set_debuglevel(1)  # Отладка SMTP отключена
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, recipients, msg.as_string())
        else:
            # Для TLS подключения (порт 587)
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                # server.set_debuglevel(1)  # Отладка SMTP отключена
                server.ehlo()
                if use_tls:
                    server.starttls(context=context)
                    server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, recipients, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        print(f"[ERROR] Ошибка аутентификации SMTP: {e}")
        print("[INFO] Проверьте правильность email и пароля приложения Gmail")
        raise
    except smtplib.SMTPConnectError as e:
        print(f"[ERROR] Ошибка подключения к SMTP: {e}")
        raise
    except smtplib.SMTPException as e:
        print(f"[ERROR] Общая ошибка SMTP: {e}")
        raise
    except Exception as e:
        print(f"[ERROR] Неожиданная ошибка: {e}")
        raise


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
    """
    _smtp_config()  # проверяем, что SMTP настроен

    subject = os.getenv('ACK_SUBJECT', 'Спасибо за обращение!')
    greeting = os.getenv('ACK_GREETING', 'Здравствуйте')
    signature = os.getenv('ACK_SIGNATURE', 'С уважением, команда проекта «Дороги Шахты»')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = os.getenv('SMTP_USER', 'no-reply@example.com')
    msg['To'] = user_email
    msg['Reply-To'] = RECIPIENT_EMAIL

    text_body = (
        f"{greeting}, {name}!\n\n"
        "Мы получили ваше сообщение и обязательно его рассмотрим.\n"
        "Спасибо за интерес к проекту по реконструкции дорог.\n\n"
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
                <h2 style="margin:12px 0 4px; color:#dbeafe; font-size:24px; line-height:1.3;">Спасибо за обращение!</h2>
                <div style="height:2px;background:linear-gradient(90deg,#60a5fa,#8b5cf6); margin:12px auto 0; width:160px;"></div>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 28px 8px;">
                <p style="margin:0 0 12px; color:#e2e8f0; font-size:16px;">{greeting}, {name}!</p>
                <p style="margin:0 0 12px; color:#cbd5e1; font-size:15px; line-height:1.6;">
                  Мы получили ваше сообщение и обязательно его рассмотрим.
                  Спасибо за интерес к проекту по реконструкции внутриквартальных дорог в нашем районе.
                </p>
                <div style="background:#0b1220;border:1px solid #1f2937;border-radius:12px;padding:16px;margin:18px 0;">
                  <div style="color:#94a3b8;font-size:12px;margin-bottom:8px;">Ваше сообщение</div>
                  <div style="white-space:pre-wrap;color:#e5e7eb;font-size:14px;line-height:1.6;">{original_message}</div>
                </div>
                <p style="margin:18px 0;">
                  <a href="https://maks-mk.github.io/sov237" style="background:linear-gradient(90deg,#4f46e5,#9333ea);color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:10px;display:inline-block;font-size:14px;">
                    Узнать больше о проекте
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
    debug_flag = os.getenv('FLASK_DEBUG', 'false').lower() in ('1','true','yes')
    app.run(host='0.0.0.0', port=port, debug=debug_flag)
