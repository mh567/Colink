import { useEffect, useState } from "react";
import { Alert, App, Button, Form, InputNumber } from "antd";
import { useTranslation } from "react-i18next";
import { knowledgeBasesApi } from "../../../../api/modules/knowledgeBases";

interface Limits {
  embedding_batch_size: number;
  embedding_max_input_chars: number;
}

export function EmbeddingRequestLimits() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<Limits>();
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    knowledgeBasesApi.getCapability().then(
      (capability) => {
        if (!active) return;
        form.setFieldsValue({
          embedding_batch_size: capability.embedding_batch_size ?? 20,
          embedding_max_input_chars: capability.embedding_max_input_chars ?? 0,
        });
        setLoaded(true);
      },
      () => {
        if (active) setError(t("models.embeddingLimitsLoadFailed"));
      },
    );
    return () => {
      active = false;
    };
  }, [form, t]);

  const save = async (values: Limits) => {
    setSaving(true);
    setError("");
    try {
      const current = await knowledgeBasesApi.getCapability();
      await knowledgeBasesApi.setFeature({
        enabled: current.feature_enabled,
        backend: current.backend,
        model: current.selected_model,
        provider_id: current.provider_id,
        ...values,
      });
      message.success(t("models.embeddingLimitsSaved"));
    } catch {
      setError(t("models.embeddingLimitsSaveFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={save}
      disabled={!loaded || saving}
    >
      {error && <Alert type="error" showIcon message={error} />}
      <Form.Item
        name="embedding_batch_size"
        label={t("models.embeddingBatchSize")}
        extra={t("models.embeddingBatchSizeHint")}
        rules={[{ required: true, type: "number", min: 1 }]}
      >
        <InputNumber min={1} precision={0} />
      </Form.Item>
      <Form.Item
        name="embedding_max_input_chars"
        label={t("models.embeddingMaxInputChars")}
        extra={t("models.embeddingMaxInputCharsHint")}
        rules={[{ required: true, type: "number", min: 0 }]}
      >
        <InputNumber min={0} precision={0} />
      </Form.Item>
      <Button htmlType="submit" loading={saving}>
        {t("models.embeddingLimitsSave")}
      </Button>
    </Form>
  );
}
