# API de predicción (FastAPI en Docker) sobre EC2 (IaaS) con:
# - Application Load Balancer en 2 AZ (alta disponibilidad, health checks a /health).
# - Auto Scaling Group con política target tracking de CPU (escalabilidad horizontal).

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_lb" "api" {
  name               = "${local.name}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "api" {
  name                 = "${local.name}-tg"
  port                 = 8000
  protocol             = "HTTP"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_launch_template" "api" {
  name_prefix   = "${local.name}-api-"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.instance_type

  iam_instance_profile {
    name = local.instance_profile_name
  }

  vpc_security_group_ids = [aws_security_group.api.id]

  metadata_options {
    http_tokens = "required" # IMDSv2
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    region      = var.region
    ecr_repo    = aws_ecr_repository.api.repository_url
    image_tag   = var.api_image_tag
    bucket      = aws_s3_bucket.data.bucket
    db_host     = aws_db_instance.main.address
    db_name     = aws_db_instance.main.db_name
    db_user     = aws_db_instance.main.username
    db_password = aws_ssm_parameter.db_password.name
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.name}-api" }
  }
}

resource "aws_autoscaling_group" "api" {
  name                      = "${local.name}-asg"
  min_size                  = var.asg_min
  desired_capacity          = var.asg_desired
  max_size                  = var.asg_max
  vpc_zone_identifier       = aws_subnet.public[*].id
  target_group_arns         = [aws_lb_target_group.api.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 180

  launch_template {
    id      = aws_launch_template.api.id
    version = "$Latest"
  }

  # Al cambiar la plantilla (nueva imagen), reemplaza instancias de forma gradual sin caída.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Name"
    value               = "${local.name}-api"
    propagate_at_launch = true
  }
}

resource "aws_autoscaling_policy" "cpu" {
  name                   = "${local.name}-cpu-target"
  autoscaling_group_name = aws_autoscaling_group.api.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageCPUUtilization"
    }
    target_value = var.cpu_target
  }
}
