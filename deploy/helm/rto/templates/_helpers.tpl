{{- define "rto.fullname" -}}{{ .Release.Name }}-rto{{- end -}}
{{- define "rto.labels" -}}
app.kubernetes.io/name: rto
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "rto.selector" -}}
app.kubernetes.io/name: rto
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "rto.image" -}}{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}{{- end -}}
{{- define "rto.env" -}}
envFrom:
  - configMapRef: {name: {{ include "rto.fullname" . }}}
  - secretRef: {name: {{ .Values.existingSecret }}}
{{- end -}}
