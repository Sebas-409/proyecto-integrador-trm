# Pipeline serverless (FaaS):
#   EventBridge (diario) -> Lambda ingest -> S3 data/raw/daily/*.json -> Lambda ETL (VPC) -> RDS
# Los paquetes se generan con scripts/build_lambdas.sh antes de terraform apply.

data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../../build/ingest"
  output_path = "${path.module}/../../build/ingest.zip"
}

data "archive_file" "etl" {
  type        = "zip"
  source_dir  = "${path.module}/../../build/etl"
  output_path = "${path.module}/../../build/etl.zip"
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${local.name}-ingest"
  role             = local.lambda_role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  timeout          = 300
  memory_size      = 256

  environment {
    variables = {
      DATA_BUCKET     = aws_s3_bucket.data.bucket
      YAHOO_SYMBOLS   = jsonencode({ brent = "BZ=F", dxy = "DX-Y.NYB", us10y = "^TNX", vix = "^VIX", sp500 = "^GSPC", usdmxn = "MXN=X", usdbrl = "BRL=X", usdcop = "COP=X" })
      BINANCE_SYMBOLS = jsonencode({ btc = "BTCUSDT", eth = "ETHUSDT" })
      NEWS_SECTIONS   = jsonencode(["https://www.larepublica.co/finanzas", "https://www.larepublica.co/economia", "https://www.larepublica.co/globoeconomia"])
    }
  }
}

resource "aws_lambda_function" "etl" {
  function_name    = "${local.name}-etl"
  role             = local.lambda_role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.etl.output_path
  source_code_hash = data.archive_file.etl.output_base64sha256
  timeout          = 300
  memory_size      = 256

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda_etl.id]
  }

  environment {
    variables = {
      DB_HOST     = aws_db_instance.main.address
      DB_NAME     = aws_db_instance.main.db_name
      DB_USER     = aws_db_instance.main.username
      DB_PASSWORD = random_password.db.result
    }
  }
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${aws_lambda_function.ingest.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "etl" {
  name              = "/aws/lambda/${aws_lambda_function.etl.function_name}"
  retention_in_days = 14
}

# ---------- Programación diaria ----------
resource "aws_cloudwatch_event_rule" "daily" {
  name                = "${local.name}-daily-ingest"
  schedule_expression = var.ingest_schedule
}

resource "aws_cloudwatch_event_target" "daily" {
  rule = aws_cloudwatch_event_rule.daily.name
  arn  = aws_lambda_function.ingest.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}

# ---------- Disparo del ETL por evento de S3 ----------
resource "aws_lambda_permission" "s3" {
  statement_id  = "AllowS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.etl.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.data.arn
}

resource "aws_s3_bucket_notification" "raw_daily" {
  bucket     = aws_s3_bucket.data.id
  depends_on = [aws_lambda_permission.s3]

  lambda_function {
    lambda_function_arn = aws_lambda_function.etl.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "data/raw/daily/"
    filter_suffix       = ".json"
  }
}
