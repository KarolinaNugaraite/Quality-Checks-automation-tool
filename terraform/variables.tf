variable "aws_region" {
  description = "AWS region for the sandbox"
  type        = string
  default     = "eu-north-1"
}

variable "bucket_name" {
  description = "Name of the existing S3 bucket containing the metadata files"
  type        = string
  default     = "sbx-new-content-integrations-test"
}

variable "original_prefix" {
  description = "S3 key prefix for original metadata JSON files"
  type        = string
  default     = "deeplink-vod/original/fi/"
}

variable "converted_prefix" {
  description = "S3 key prefix for converted metadata JSON files"
  type        = string
  default     = "deeplink-vod/converted/fi/"
}

variable "iam_user_name" {
  description = "IAM user name created for the metadata checker"
  type        = string
  default     = "metadata-checker-reader"
}
