terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── IAM policy: read-only access to the metadata bucket ────────────────────────

data "aws_iam_policy_document" "metadata_checker_read" {
  statement {
    sid    = "ListBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = ["arn:aws:s3:::${var.bucket_name}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "${var.original_prefix}*",
        "${var.converted_prefix}*",
      ]
    }
  }

  statement {
    sid    = "GetObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = [
      "arn:aws:s3:::${var.bucket_name}/${var.original_prefix}*",
      "arn:aws:s3:::${var.bucket_name}/${var.converted_prefix}*",
    ]
  }
}

resource "aws_iam_policy" "metadata_checker_read" {
  name        = "${var.iam_user_name}-policy"
  description = "Read-only access to metadata checker prefixes in ${var.bucket_name}"
  policy      = data.aws_iam_policy_document.metadata_checker_read.json
}

# ── IAM user + access key ───────────────────────────────────────────────────────

resource "aws_iam_user" "metadata_checker" {
  name = var.iam_user_name
  tags = {
    Purpose = "metadata-checker"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_user_policy_attachment" "metadata_checker_read" {
  user       = aws_iam_user.metadata_checker.name
  policy_arn = aws_iam_policy.metadata_checker_read.arn
}

resource "aws_iam_access_key" "metadata_checker" {
  user = aws_iam_user.metadata_checker.name
}
