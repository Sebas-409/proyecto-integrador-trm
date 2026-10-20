terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Para trabajo en equipo, descomentar y usar un bucket S3 como backend remoto del estado.
  # backend "s3" {
  #   bucket = "trm-tfstate-<sufijo>"
  #   key    = "proyecto-integrador/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Course      = "proyecto-integrador-v-semestre"
    }
  }
}

# Región secundaria para disaster recovery (réplica de S3).
provider "aws" {
  alias  = "dr"
  region = var.dr_region
}
