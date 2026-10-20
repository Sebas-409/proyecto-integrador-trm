# Data lake en S3 (raw/ -> processed/ -> models/), cifrado, versionado y con ciclo de vida.
# Disaster recovery: réplica entre regiones (CRR) hacia var.dr_region.

resource "random_id" "suffix" {
  byte_length = 3
}

resource "aws_s3_bucket" "data" {
  bucket        = "${local.name}-data-${random_id.suffix.hex}"
  force_destroy = true # proyecto académico: permite terraform destroy aunque haya objetos
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "raw-tiering"
    status = "Enabled"
    filter {
      prefix = "data/raw/"
    }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 180
      storage_class = "GLACIER_IR"
    }
  }

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# ---------------- Réplica DR ----------------

resource "aws_s3_bucket" "dr" {
  count         = var.enable_dr_replication ? 1 : 0
  provider      = aws.dr
  bucket        = "${local.name}-dr-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "dr" {
  count    = var.enable_dr_replication ? 1 : 0
  provider = aws.dr
  bucket   = aws_s3_bucket.dr[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "dr" {
  count                   = var.enable_dr_replication ? 1 : 0
  provider                = aws.dr
  bucket                  = aws_s3_bucket.dr[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_replication_configuration" "data" {
  count      = var.enable_dr_replication ? 1 : 0
  depends_on = [aws_s3_bucket_versioning.data, aws_s3_bucket_versioning.dr]
  role       = local.replication_role_arn
  bucket     = aws_s3_bucket.data.id

  rule {
    id     = "dr-models-and-data"
    status = "Enabled"
    filter {}
    delete_marker_replication {
      status = "Enabled"
    }
    destination {
      bucket        = aws_s3_bucket.dr[0].arn
      storage_class = "STANDARD_IA"
    }
  }
}

# ---------------- Registro de imágenes ----------------

resource "aws_ecr_repository" "api" {
  name                 = "${local.name}-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Conservar solo las ultimas 5 imagenes"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}
