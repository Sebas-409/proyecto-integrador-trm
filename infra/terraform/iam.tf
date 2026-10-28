# Roles IAM con mínimo privilegio. En AWS Academy (sin permisos para crear roles)
# se define var.lab_role_name = "LabRole" y var.lab_instance_profile = "LabInstanceProfile".

locals {
  use_lab_role = var.lab_role_name != ""
}

data "aws_iam_role" "lab" {
  count = local.use_lab_role ? 1 : 0
  name  = var.lab_role_name
}

data "aws_iam_policy_document" "assume" {
  for_each = toset(["ec2.amazonaws.com", "lambda.amazonaws.com", "s3.amazonaws.com"])
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = [each.value]
    }
  }
}

# ---------- API (EC2) ----------
resource "aws_iam_role" "api" {
  count              = local.use_lab_role ? 0 : 1
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.assume["ec2.amazonaws.com"].json
}

resource "aws_iam_role_policy_attachment" "api_ecr" {
  count      = local.use_lab_role ? 0 : 1
  role       = aws_iam_role.api[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "api_ssm_core" {
  count      = local.use_lab_role ? 0 : 1
  role       = aws_iam_role.api[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "api" {
  count = local.use_lab_role ? 0 : 1
  role  = aws_iam_role.api[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"],
      Resource = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"] },
      { Effect = "Allow", Action = ["ssm:GetParameter"],
      Resource = [aws_ssm_parameter.db_password.arn, aws_ssm_parameter.anthropic_api_key.arn] }
    ]
  })
}

resource "aws_iam_instance_profile" "api" {
  count = local.use_lab_role ? 0 : 1
  name  = "${local.name}-api"
  role  = aws_iam_role.api[0].name
}

# ---------- Lambdas ----------
resource "aws_iam_role" "lambda" {
  count              = local.use_lab_role ? 0 : 1
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume["lambda.amazonaws.com"].json
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  count      = local.use_lab_role ? 0 : 1
  role       = aws_iam_role.lambda[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda" {
  count = local.use_lab_role ? 0 : 1
  role  = aws_iam_role.lambda[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      Resource = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
    }]
  })
}

# ---------- Replicación S3 ----------
resource "aws_iam_role" "replication" {
  count              = local.use_lab_role || !var.enable_dr_replication ? 0 : 1
  name               = "${local.name}-s3-replication"
  assume_role_policy = data.aws_iam_policy_document.assume["s3.amazonaws.com"].json
}

resource "aws_iam_role_policy" "replication" {
  count = local.use_lab_role || !var.enable_dr_replication ? 0 : 1
  role  = aws_iam_role.replication[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetReplicationConfiguration", "s3:ListBucket"], Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"],
      Resource = "${aws_s3_bucket.data.arn}/*" },
      { Effect = "Allow", Action = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"],
      Resource = "${aws_s3_bucket.dr[0].arn}/*" }
    ]
  })
}

locals {
  lambda_role_arn       = local.use_lab_role ? data.aws_iam_role.lab[0].arn : aws_iam_role.lambda[0].arn
  instance_profile_name = local.use_lab_role ? var.lab_instance_profile : aws_iam_instance_profile.api[0].name
  replication_role_arn = local.use_lab_role ? data.aws_iam_role.lab[0].arn : (
    var.enable_dr_replication ? aws_iam_role.replication[0].arn : ""
  )
}
