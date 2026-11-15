# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
V4 Signed URL generation for Cloud Storage objects.

Signed URLs grant time-limited, unauthenticated access to private GCS
objects — use them to share objects with external systems or users without
making the bucket public and without requiring the recipient to have GCP
credentials.

Auth note: Signing requires a private key. Plain `gcloud auth
application-default login` user credentials CANNOT sign URLs directly.
Two supported paths:
  1. Service account key file (GOOGLE_APPLICATION_CREDENTIALS).
  2. Impersonated credentials — caller needs roles/iam.serviceAccountTokenCreator
     on the target service account (no key file downloaded).

This module auto-detects which path is available and falls back gracefully
with clear error messages.

Supported methods: GET (default), PUT (pre-signed upload), DELETE.
"""

import datetime
import os

import google.auth
from google.auth import impersonated_credentials
from google.cloud import storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

# Service account to impersonate when ADC has no private key.
# The App Engine default SA exists in most projects; override with SIGNER_SA.
SIGNER_SA = os.environ.get(
    "SIGNER_SA",
    f"{PROJECT_ID}@appspot.gserviceaccount.com",
)


def _get_signing_credentials():
    """
    Return credentials capable of signing URLs, or None to use client default.

    If ADC already has a private key (service account key file), signing works
    without impersonation. Otherwise, fall back to impersonated credentials.
    """
    creds, _ = google.auth.default()
    if hasattr(creds, "sign_bytes"):
        print("ADC has a private key — signing directly.")
        return None

    print("ADC has no private key (user credentials). Using impersonated credentials.")
    print(f"Impersonating : {SIGNER_SA}")
    print("If this fails, grant yourself roles/iam.serviceAccountTokenCreator")
    print(f"on {SIGNER_SA}  (see deploy.sh for the exact command).\n")

    return impersonated_credentials.Credentials(
        source_credentials=creds,
        target_principal=SIGNER_SA,
        target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
    )


def generate_signed_url(
    blob_name: str,
    method: str = "GET",
    expiration_minutes: int = 15,
) -> str:
    """
    Generate a V4 signed URL for the given blob.

    Args:
        blob_name: Object path inside the bucket (e.g. 'data/sales_data.csv').
        method:    HTTP method: 'GET', 'PUT', or 'DELETE'.
        expiration_minutes: URL validity in minutes (max 10080 = 7 days for V4).

    Returns:
        The signed URL as a string.

    Raises:
        SystemExit on signing failure with a remediation message.
    """
    client = storage.Client(project=PROJECT_ID)
    blob = client.bucket(GCS_BUCKET).blob(blob_name)
    signing_creds = _get_signing_credentials()
    kwargs = {"credentials": signing_creds} if signing_creds else {}

    try:
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=expiration_minutes),
            method=method,
            **kwargs,
        )
    except Exception as exc:
        print(f"\nSigning failed: {exc}\n")
        print("Remediation: grant your user permission to impersonate the SA:")
        print(f"  gcloud iam service-accounts add-iam-policy-binding {SIGNER_SA} \\")
        print("    --member=user:$(gcloud config get-value account) \\")
        print("    --role=roles/iam.serviceAccountTokenCreator")
        raise SystemExit(1)

    print(f"\n--- V4 Signed URL ({method}, valid {expiration_minutes} min) ---")
    print(f"Object  : gs://{GCS_BUCKET}/{blob_name}")
    print(f"URL     : {url}")
    print(f"\nTest it:")
    if method == "GET":
        print(f'  curl -s "{url}" | head')
    elif method == "PUT":
        print(f'  curl -X PUT -H "Content-Type: text/csv" --upload-file <file> "{url}"')
    return url
