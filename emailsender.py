def send_email(user, pwd, recipient, subject, body):
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header

    gmail_user = user
    gmail_pwd = pwd
    FROM = user
    TO = recipient if type(recipient) is list else [recipient]
    SUBJECT = subject
    TEXT = body

    # Prepare actual message.  The daily report contains Chinese word names,
    # so the body is sent as UTF-8 instead of relying on an ASCII-only string.
    if not isinstance(TEXT, unicode):
        TEXT = TEXT.decode("utf-8")
    if not isinstance(SUBJECT, unicode):
        SUBJECT = SUBJECT.decode("utf-8")
    message = MIMEText(TEXT, "plain", "utf-8")
    message["Subject"] = Header(SUBJECT, "utf-8")
    message["From"] = FROM
    message["To"] = ", ".join(TO)
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.ehlo()
        server.starttls()
        server.login(gmail_user, gmail_pwd)
        server.sendmail(FROM, TO, message.as_string())
        server.close()
        print 'successfully sent the mail'
    except Exception,e:
        print e
        print "failed to send mail"
