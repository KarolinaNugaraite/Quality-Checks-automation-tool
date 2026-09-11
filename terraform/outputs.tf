output "iam_user_name" {
  description = "IAM user created for the metadata checker"
  value       = aws_iam_user.metadata_checker.name
}

output "aws_access_key_id" {
  description = "AWS_ACCESS_KEY_ID — export this before running the checker"
  value       = aws_iam_access_key.metadata_checker.id
  sensitive   = true
}

output "aws_secret_access_key" {
  description = "AWS_SECRET_ACCESS_KEY — export this before running the checker"
  value       = aws_iam_access_key.metadata_checker.secret
  sensitive   = true
}

output "cli_run_original_example" {
  description = "Example CLI command for original checks via S3"
  value       = <<-EOT
    python -m metadata_checker.cli run-original \
      --mode s3 \
      --bucket ${var.bucket_name} \
      --prefix ${var.original_prefix} \
      --sample-size 100 \
      --output reports/original_report.json
  EOT
}

output "cli_run_converted_example" {
  description = "Example CLI command for converted checks via S3"
  value       = <<-EOT
    python -m metadata_checker.cli run-converted \
      --mode s3 \
      --bucket ${var.bucket_name} \
      --original-prefix ${var.original_prefix} \
      --converted-prefix ${var.converted_prefix} \
      --sample-size 100 \
      --output reports/converted_report.json
  EOT
}
