{{/*
Expand the name of the chart.
*/}}
{{- define "netdrift.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a fully-qualified app name. Truncated to 63 characters — the DNS limit.
*/}}
{{- define "netdrift.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label (name-version, + replaced with _ for label safety).
*/}}
{{- define "netdrift.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "netdrift.labels" -}}
helm.sh/chart: {{ include "netdrift.chart" . }}
{{ include "netdrift.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — used in matchLabels and Service selectors.
*/}}
{{- define "netdrift.selectorLabels" -}}
app.kubernetes.io/name: {{ include "netdrift.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Fully-qualified image reference. Falls back to appVersion when image.tag is blank.
*/}}
{{- define "netdrift.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{/*
DATABASE_URL constructed from the postgresql subchart values.
The Bitnami subchart names the service <release>-postgresql.
*/}}
{{- define "netdrift.databaseUrl" -}}
{{- $host := printf "%s-postgresql" .Release.Name }}
{{- printf "postgresql+psycopg://%s:%s@%s:5432/%s"
      .Values.postgresql.auth.username
      .Values.postgresql.auth.password
      $host
      .Values.postgresql.auth.database }}
{{- end }}
