"""
Date: 2023-10-23 18:24:31
LastEditors: Kumo
LastEditTime: 2024-09-28 17:21:58
Description: 
"""

from auto_score.deploy_stragegies import *
from auto_score.utils.proxy_decorator import IS_AUTHOR_ENV

from auto_score.utils.singleton import get_handler, GetHandlers
from auto_score.utils.logger import LoggerManager
from auto_score.directly_request.mms import MMS
from auto_score.collector import (
    parse_last_download,
    process_subscriptions,
    build_progress_updates,
    write_last_download_signal,
)

from auto_score.ms_auth import MSAuth
from auto_score.onedrive.patch import upload_large_file
from auto_score.email import EmailHandler

import os
import base64

log_manager = LoggerManager(f"log/{__name__}.log")
logger = log_manager.logger
ERROR_MSGS = []


def generate_xoauth2(username, token):
    xoauth = "user=%s\x01auth=Bearer %s\x01\x01" % (username, token)
    xoauth = xoauth.encode("ascii")
    xoauth = base64.b64encode(xoauth)
    xoauth = xoauth.decode("ascii")
    # print("XOAUTH2: ", xoauth)
    return xoauth


def collect_errors(err):
    logger.error(err)
    ERROR_MSGS.append(err)


if __name__ == "__main__":
    ## 0. get config
    env = os.environ.get("AUTO_HALCYON_ENV")
    if not env and IS_AUTHOR_ENV:
        env = "LOCAL"

    assert env
    if env == "LOCAL":
        strategy = LocalStrategy()
    elif env == "DOCKER":
        strategy = DockerStrategy()
    elif env == "GITHUB_ACTION":
        strategy = GithubActionStrategy()
    else:
        collect_errors(f"env error, not support env: {env}")
        os._exit(-1)

    ## 1. Init
    ### website handlers
    mms_handler = MMS(strategy.email, strategy.password, strategy.savefolder_path)

    ### messy params
    sub_filename = "./subscriptions"  # source | user
    last_download_filename = "./_last_download_signal"

    ## 2. load subscriptions and last downloading times
    assert os.path.exists(sub_filename)
    with open(sub_filename, "r", encoding="utf-8") as file:
        subscriptions = file.readlines()
    if os.path.exists(last_download_filename):
        with open(last_download_filename, "r") as file:
            last_download_lines = file.readlines()
        last_downloads = (
            parse_last_download(last_download_lines) if last_download_lines else {}
        )
    else:
        last_download_lines = {}

    ## 3. for each sub, try to find new links in rss data and call cloudreve offline download
    # Each subscription is processed independently: a fetch exception, a
    # failed download or an empty result only marks that author as failed
    # and never interrupts the remaining authors.
    outcomes = process_subscriptions(
        subscriptions,
        last_downloads,
        get_handler,
        error_collector=collect_errors,
    )

    all_tasks_success = True
    num_newly_downloads = 0
    titles_newly_download = []
    # variables kept for the summary/email section below
    max_sheetnum = 0
    last_sheetnum = -1
    for outcome in outcomes:
        if outcome.failed:
            all_tasks_success = False
            logger.error(
                "Subscription failed | source=%s user=%s status=%s reason=%s",
                outcome.source,
                outcome.user,
                outcome.status,
                outcome.error,
            )
        elif outcome.status == "success":
            num_newly_downloads += outcome.sheet_count
            titles_newly_download.extend(outcome.titles)
            max_sheetnum = max(max_sheetnum, outcome.max_sheetnum or 0)

    failed_authors = [
        f"{o.source}|{o.user} ({o.status})" for o in outcomes if o.failed
    ]
    empty_authors = [
        f"{o.source}|{o.user}" for o in outcomes if o.status == "empty"
    ]
    if failed_authors:
        logger.error(
            "Failed subscriptions this run: %s", "; ".join(failed_authors)
        )
    if empty_authors:
        logger.warning(
            "Subscriptions with no new sheet this run: %s",
            "; ".join(empty_authors),
        )

    ### 4. collect all sheets
    all_sheets_dir = []
    for handler in GetHandlers():
        all_sheets_dir.extend([path for path in handler.file_paths])

    ## 5. upload to onedrive
    if strategy.enable_od_upload:
        od_ms_auth = MSAuth(
            strategy.od_client_id,
            strategy.od_client_secret,
            strategy.od_redirect_uri,
            ["files.readwrite", "user.read", "offline_access"],
            "_od_refresh_token",
        )
        all_od_upload_success = True
        for path in all_sheets_dir:
            access_token = od_ms_auth.get_access_token()
            if access_token:
                # get filename from path with extension
                upload_target = os.path.join(
                    strategy.od_upload_dir, os.path.basename(path)
                ).replace("\\", "/")
                logger.debug(upload_target)
                if upload_large_file(access_token, path, upload_target):
                    logger.info("Upload to onedrive successfully")
                else:
                    all_od_upload_success = False
                    collect_errors("Failed to upload to onedrive")
            else:
                collect_errors("cannot refresh onedrive token")

    ## 6. update download data
    # Only subscriptions processed this run are written: successful authors
    # advance to their new max sheetId, while failed/empty authors keep
    # their previously persisted value (never reset to 0, never deleted).
    latest_downloads = build_progress_updates(outcomes, last_downloads)
    write_last_download_signal(last_download_filename, latest_downloads)

    ## 7. send email
    has_error_prefix = "[ERROR] " if len(ERROR_MSGS) > 0 else ""
    if strategy.enable_email_notify:
        ### check result and prepare mail data
        logger.info("=" * 50)
        logger.info("summary: ")
        if all_tasks_success:
            if num_newly_downloads > 0 and max_sheetnum > last_sheetnum:
                subject = f"{has_error_prefix}Successfully downloading sheets."
                content = "Success downloading the following sheet(s):\n{}".format(
                    "\n".join([title for title in titles_newly_download])
                )
                logger.info("All sheets start to download successfully.")

            else:  # nothing new
                subject = f"{has_error_prefix}There's no new sheet or old sheet redownloaded to update."
                content = "There's no new sheet!"
                logger.info("There's no new sheet")

        else:  # download error
            subject = f"{has_error_prefix}Failed to download all sheets."
            content = "Failed..."
            collect_errors("Failed to download all sheets.")

        if has_error_prefix:
            content += "ERROR msgs: \n{}".format("\n".join([err for err in ERROR_MSGS]))
        logger.info("=" * 50)

        if strategy.use_oauth2_outlook:
            ms_auther = MSAuth(
                strategy.outlook_client_id,
                strategy.outlook_client_secret,
                strategy.outlook_redirect_uri,
                ["https://outlook.office.com/SMTP.Send", "offline_access"],
                "_outlook_refresh_token",
            )
            ms_access_token = ms_auther.get_access_token()
            if not ms_access_token:
                os._exit(-1)
            else:
                xoauth = generate_xoauth2(strategy.sender, ms_access_token)
                email_handler = EmailHandler(
                    strategy.sender,
                    strategy.smtp_host,
                    strategy.smtp_port,
                    strategy.mail_license,
                    strategy.receivers,
                    xoauth=xoauth,
                )
        else:
            email_handler = EmailHandler(
                strategy.sender,
                strategy.smtp_host,
                strategy.smtp_port,
                strategy.mail_license,
                strategy.receivers,
            )
        # all_sheets_dir.extend(LoggerManager.get_all_log_filenames())
        if not email_handler.perform_sending(
            subject,
            content,
            sheet_files=all_sheets_dir,
            log_files=(
                LoggerManager.get_all_log_filenames() if strategy.send_logs else []
            ),
        ):
            os._exit(-1)
    else:
        logger.info("Skip sending email.")
        if has_error_prefix:
            logger.error(
                "ERROR msgs: \n{}".format("\n".join([err for err in ERROR_MSGS]))
            )
            raise Exception("Error occurred, please check log.")
