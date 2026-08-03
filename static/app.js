const iconPaths = {
    "archive": '<path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/>',
    "arrow-left": '<path d="M19 12H5"/><path d="m11 18-6-6 6-6"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    "arrow-up": '<path d="m18 15-6-6-6 6"/><path d="M12 9v12"/>',
    "arrow-up-right": '<path d="M7 17 17 7"/><path d="M7 7h10v10"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "close": '<path d="M18 6 6 18M6 6l12 12"/>',
    "code": '<path d="m8 9-3 3 3 3M16 9l3 3-3 3M14 5l-4 14"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "expand": '<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2M3 12H1"/>',
    "home": '<path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10M9 20v-6h6v6"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
    "lock": '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "mouse-pointer": '<path d="m4 3 7.5 17 2.6-6.1L20 11.5z"/>',
    "package": '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/><path d="M7 9.5v3.6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "plus-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "receipt": '<path d="M6 2h12v20l-3-2-3 2-3-2-3 2z"/><path d="M9 7h6M9 11h6M9 15h4"/>',
    "refresh": '<path d="M20 7h-5V2"/><path d="M20 7a9 9 0 1 0 1 8"/>',
    "scan": '<path d="M3 8V4h4M17 4h4v4M21 16v4h-4M7 20H3v-4"/><path d="M7 12h10"/>',
    "sheet": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
    "sparkles": '<path d="m12 3 1.1 3.1L16 7.5l-2.9 1.4L12 12l-1.1-3.1L8 7.5l2.9-1.4z"/><path d="m19 13 .7 2.1L22 16l-2.3.9L19 19l-.7-2.1L16 16l2.3-.9zM5 14l.8 2.3L8 17l-2.2.7L5 20l-.8-2.3L2 17l2.2-.7z"/>',
    "folder-plus": '<path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v6M9 14h6"/>',
    "taxi": '<path d="M4 16v2M20 16v2"/><path d="M5 16a1 1 0 0 1-1-1v-3l1-5h14l1 5v3a1 1 0 0 1-1 1z"/><path d="M5 12h14M9 12V8M15 12V8M7 16h.01M17 16h.01"/>',
    "train": '<rect x="5" y="3" width="14" height="15" rx="3"/><path d="M8 21l2-3M16 21l-2-3M8 7h8M8 12h.01M16 12h.01"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/>',
    "upload-cloud": '<path d="M16 16l-4-4-4 4M12 12v9"/><path d="M20.4 17.5A5 5 0 0 0 18 8.2 7 7 0 0 0 4.3 10.5 4.5 4.5 0 0 0 5.5 19H7"/>'
};

function renderIcons(root = document) {
    root.querySelectorAll("[data-icon]").forEach((node) => {
        const name = node.dataset.icon;
        const path = iconPaths[name];
        if (!path || node.dataset.iconReady) return;
        node.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
        node.dataset.iconReady = "true";
    });
}

function showToast(message, type = "success") {
    const region = document.getElementById("toast-region");
    if (!region || !message) return;
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span data-icon="${type === "error" ? "close" : "check"}"></span><span></span>`;
    toast.lastElementChild.textContent = message;
    region.appendChild(toast);
    renderIcons(toast);
    window.setTimeout(() => toast.remove(), 4200);
}

function setupDialogs() {
    document.querySelectorAll("[data-dialog-open]").forEach((button) => {
        button.addEventListener("click", () => {
            const dialog = document.getElementById(button.dataset.dialogOpen);
            if (!dialog) return;
            dialog.showModal();
            window.setTimeout(() => dialog.querySelector("input:not([type=checkbox])")?.focus(), 50);
        });
    });
    document.querySelectorAll("[data-dialog-close]").forEach((button) => {
        button.addEventListener("click", () => button.closest("dialog")?.close());
    });
    document.querySelectorAll("dialog").forEach((dialog) => {
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) dialog.close();
        });
    });
}

function setupConfirmations() {
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!window.confirm(form.dataset.confirm)) event.preventDefault();
        });
    });
    document.querySelectorAll("[data-confirm-button]").forEach((button) => {
        button.addEventListener("click", (event) => {
            if (!window.confirm(button.dataset.confirmButton)) event.preventDefault();
        });
    });
}

function setupUpload() {
    const form = document.querySelector("[data-upload-form]");
    const input = form?.querySelector("[data-file-input]");
    if (!form || !input) return;

    const pick = () => input.click();
    document.querySelectorAll("[data-file-picker], [data-upload-trigger]").forEach((button) => button.addEventListener("click", pick));
    input.addEventListener("change", () => {
        if (!input.files.length) return;
        showToast(`已选择 ${input.files.length} 个文件，正在识别…`);
        form.submit();
    });
    ["dragenter", "dragover"].forEach((eventName) => form.addEventListener(eventName, (event) => {
        event.preventDefault();
        form.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach((eventName) => form.addEventListener(eventName, (event) => {
        event.preventDefault();
        form.classList.remove("dragover");
    }));
    form.addEventListener("drop", (event) => {
        if (!event.dataTransfer?.files.length) return;
        input.files = event.dataTransfer.files;
        showToast(`已接收 ${input.files.length} 个文件，正在识别…`);
        form.submit();
    });
}

function getDocuments() {
    const data = document.getElementById("documents-data");
    if (!data) return {};
    try { return JSON.parse(data.textContent); } catch { return {}; }
}

function ocrField(documentData, key) {
    return documentData?.ocr?.[key]?.value ?? documentData?.[key] ?? "";
}

function setupReviewWorkspace(documents) {
    const rows = [...document.querySelectorAll("[data-document-id]")];
    const preview = document.querySelector("[data-file-preview]");
    const confirmForm = document.querySelector("[data-confirm-form]");
    if (!rows.length || !preview || !confirmForm) return;

    const fields = Object.fromEntries([...confirmForm.querySelectorAll("[data-field]")].map((field) => [field.dataset.field, field]));
    const rerunForm = document.querySelector("[data-rerun-form]");
    const deleteForm = document.querySelector("[data-delete-form]");
    const lockedNotice = document.querySelector("[data-locked-notice]");
    const confirmButton = document.querySelector("[data-review-confirm-button]");
    const confidencePill = document.querySelector("[data-confidence-pill]");
    const subtitle = document.querySelector("[data-analysis-subtitle]");
    const ocrText = document.querySelector("[data-ocr-text]");
    const filename = document.querySelector("[data-preview-filename]");
    const routeField = document.querySelector("[data-route-field]");
    const merchantField = document.querySelector("[data-merchant-field]");

    const syncTypeFields = () => {
        const showRoute = fields.doc_type.value !== "invoice";
        routeField.hidden = !showRoute;
        merchantField.hidden = showRoute;
    };
    fields.doc_type.addEventListener("change", syncTypeFields);

    const openDocument = (id, updateUrl = true) => {
        const documentData = documents[String(id)];
        if (!documentData) return;
        rows.forEach((row) => row.classList.toggle("selected", row.dataset.documentId === String(id)));

        filename.textContent = documentData.original_filename || documentData.filename;
        preview.replaceChildren();
        const fileNode = document.createElement(documentData.is_pdf ? "iframe" : "img");
        fileNode.src = documentData.file_url;
        if (documentData.is_pdf) fileNode.title = "PDF 票据预览";
        else fileNode.alt = documentData.original_filename || documentData.filename;
        preview.appendChild(fileNode);
        Object.entries(fields).forEach(([key, field]) => { field.value = ocrField(documentData, key); });
        syncTypeFields();
        confirmForm.action = `/documents/${id}/confirm`;
        rerunForm.action = `/documents/${id}/ocr`;
        deleteForm.action = `/documents/${id}/delete`;
        ocrText.textContent = documentData.ocr_text || "暂无 OCR 原始文本。";

        const locked = Boolean(documentData.locked);
        Object.values(fields).forEach((field) => { field.disabled = locked || !documentData.can_edit; });
        confirmButton.disabled = locked || !documentData.can_confirm;
        confirmButton.innerHTML = locked ? '<span data-icon="lock"></span>票据已锁定' : '<span data-icon="check"></span>确认并自动归档';
        lockedNotice.hidden = !locked;
        rerunForm.hidden = !documentData.can_rerun;
        deleteForm.hidden = !documentData.can_delete;

        const confidence = Number(documentData.ocr?.overall_confidence || 0);
        confidencePill.className = "confidence-pill";
        if (documentData.ocr_status === "confirmed") {
            confidencePill.textContent = "已人工确认";
            confidencePill.classList.add("confirmed");
            subtitle.textContent = "信息已保存，可勾选加入报销单";
        } else if (documentData.ocr_status === "failed") {
            confidencePill.textContent = "识别失败";
            confidencePill.classList.add("low");
            subtitle.textContent = documentData.ocr_error || "请手动填写关键信息";
        } else {
            confidencePill.textContent = confidence ? `AI 置信度 ${Math.round(confidence * 100)}%` : "AI 识别";
            if (confidence && confidence < 0.82) confidencePill.classList.add("low");
            subtitle.textContent = confidence && confidence < 0.82 ? "部分字段可信度较低，请重点核对" : "请核对关键信息";
        }
        renderIcons(confirmForm);
        if (updateUrl) history.replaceState(null, "", `${location.pathname}?document=${id}#receipt-workbench`);
    };

    document.querySelectorAll("[data-open-document]").forEach((button) => {
        button.addEventListener("click", () => openDocument(button.dataset.openDocument));
    });
    const selected = rows.find((row) => row.classList.contains("selected")) || rows[0];
    openDocument(selected.dataset.documentId, false);
}

function setupDocumentList(documents) {
    const checks = [...document.querySelectorAll("[data-document-check]")];
    const selectAll = document.querySelector("[data-select-all]");
    const selectedCount = document.querySelector("[data-selected-count]");
    const selectedLabel = document.querySelector("[data-selected-label]");
    const selectedTotal = document.querySelector("[data-selected-total]");
    const submitButton = document.querySelector("[data-reimbursement-form] button[type=submit]");
    if (!checks.length) return;

    const updateSelection = () => {
        const selected = checks.filter((check) => check.checked);
        const total = selected.reduce((sum, check) => sum + Number(documents[check.value]?.amount || 0), 0);
        selectedCount.textContent = selected.length;
        selectedLabel.textContent = selected.length;
        selectedTotal.textContent = total.toFixed(2);
        if (submitButton && !submitButton.hasAttribute("data-server-disabled")) submitButton.disabled = selected.length === 0;
        const available = checks.filter((check) => !check.disabled);
        if (selectAll) {
            selectAll.checked = available.length > 0 && available.every((check) => check.checked);
            selectAll.indeterminate = selected.length > 0 && !selectAll.checked;
        }
    };
    checks.forEach((check) => check.addEventListener("change", updateSelection));
    selectAll?.addEventListener("change", () => {
        checks.filter((check) => !check.disabled).forEach((check) => { check.checked = selectAll.checked; });
        updateSelection();
    });
    updateSelection();

    const filterTabs = [...document.querySelectorAll("[data-filter]")];
    filterTabs.forEach((tab) => {
        tab.addEventListener("click", () => {
            filterTabs.forEach((item) => item.classList.toggle("active", item === tab));
            document.querySelectorAll("[data-document-id]").forEach((row) => {
                const filter = tab.dataset.filter;
                const visible = filter === "unsubmitted"
                    ? row.dataset.status !== "reimbursed"
                    : filter === "review"
                        ? ["ready", "needs_review", "failed"].includes(row.dataset.ocrStatus)
                        : filter === "reimbursed"
                            ? row.dataset.status === "reimbursed"
                            : true;
                row.classList.toggle("filtered-out", !visible);
            });
        });
    });
    filterTabs.find((tab) => tab.classList.contains("active"))?.click();
    document.querySelector("[data-list-density]")?.addEventListener("click", () => document.querySelector("[data-document-list]")?.classList.toggle("compact"));
}

function showQueryFeedback() {
    const params = new URLSearchParams(location.search);
    if (params.get("error")) showToast(params.get("error"), "error");
    else if (params.get("generated")) showToast("报销单已生成，请下载检查后确认结果。");
    else if (params.get("resolved")) showToast("报销结果已确认，成功票据已锁定。");
    else if (params.get("cancelled")) showToast("报销单已撤回，票据恢复为可编辑状态。");
    else if (params.get("reverted")) showToast("历史报销已撤回，票据已恢复。");
}

document.addEventListener("DOMContentLoaded", () => {
    renderIcons();
    setupDialogs();
    setupConfirmations();
    setupUpload();
    const documents = getDocuments();
    setupReviewWorkspace(documents);
    setupDocumentList(documents);
    showQueryFeedback();
});
