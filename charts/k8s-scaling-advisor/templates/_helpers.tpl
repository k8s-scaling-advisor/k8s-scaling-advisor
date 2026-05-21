{{- define "k8s-scaling-advisor.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "k8s-scaling-advisor.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "k8s-scaling-advisor.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "k8s-scaling-advisor.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "k8s-scaling-advisor.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "k8s-scaling-advisor.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}
{{- end -}}

{{- /*
Uploader sidecar image. Default depends on kind:
  s3    -> amazon/aws-cli:2.17.0
  http  -> curlimages/curl:8.10.1
  slack -> curlimages/curl:8.10.1
Operator can override via .Values.uploader.image.repository / .tag.

If the operator sets a custom repository without a tag, we fail the
template render rather than silently falling back to `:latest`.
Reproducibility / rollback safety > convenience.
*/ -}}
{{- define "k8s-scaling-advisor.uploaderImage" -}}
{{- $repo := .Values.uploader.image.repository -}}
{{- $tag := .Values.uploader.image.tag -}}
{{- if not $repo -}}
  {{- if eq .Values.uploader.kind "s3" -}}
    {{- $repo = "amazon/aws-cli" -}}
    {{- if not $tag }}{{- $tag = "2.17.0" -}}{{- end -}}
  {{- else -}}
    {{- $repo = "curlimages/curl" -}}
    {{- if not $tag }}{{- $tag = "8.10.1" -}}{{- end -}}
  {{- end -}}
{{- end -}}
{{- if not $tag -}}
  {{- fail (printf "uploader.image.tag is required when uploader.image.repository is set (got repository=%q with empty tag). Pin a specific version; refusing to fall back to :latest." $repo) -}}
{{- end -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
