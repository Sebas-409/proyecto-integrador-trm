# Observabilidad y control de costos: alarmas de CloudWatch, tablero y presupuesto mensual.

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name}-alb-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${local.name}-unhealthy-hosts"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "${local.name}-ingest-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      { type = "metric", x = 0, y = 0, width = 12, height = 6, properties = {
        title = "Peticiones y latencia del ALB", region = var.region, stat = "Sum", period = 60,
        metrics = [["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.api.arn_suffix],
        [".", "TargetResponseTime", ".", ".", { stat = "Average", yAxis = "right" }]]
      } },
      { type = "metric", x = 12, y = 0, width = 12, height = 6, properties = {
        title = "CPU e instancias del ASG", region = var.region, period = 60,
        metrics = [["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", aws_autoscaling_group.api.name, { stat = "Average" }],
        ["AWS/AutoScaling", "GroupInServiceInstances", "AutoScalingGroupName", aws_autoscaling_group.api.name, { stat = "Maximum", yAxis = "right" }]]
      } },
      { type = "metric", x = 0, y = 6, width = 12, height = 6, properties = {
        title = "Lambdas (invocaciones / errores)", region = var.region, stat = "Sum", period = 3600,
        metrics = [["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.ingest.function_name],
          [".", "Errors", ".", aws_lambda_function.ingest.function_name],
          [".", "Invocations", ".", aws_lambda_function.etl.function_name],
        [".", "Errors", ".", aws_lambda_function.etl.function_name]]
      } },
      { type = "metric", x = 12, y = 6, width = 12, height = 6, properties = {
        title = "RDS", region = var.region, stat = "Average", period = 300,
        metrics = [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.identifier],
        [".", "DatabaseConnections", ".", ".", { yAxis = "right" }]]
      } }
    ]
  })
}

resource "aws_budgets_budget" "monthly" {
  count        = var.enable_budget ? 1 : 0
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Project$%s", var.project)] # requiere activar el tag Project como cost allocation tag
  }

  dynamic "notification" {
    for_each = var.alert_email != "" ? [50, 80, 100] : []
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value == 100 ? "FORECASTED" : "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
