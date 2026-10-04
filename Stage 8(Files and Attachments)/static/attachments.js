/* Upload UI only. File contents never enter a model prompt. */
window.AttachmentUI = (() => {
    const MAX_FILE = 10 * 1024 * 1024, MAX_TOTAL = 25 * 1024 * 1024, MAX_COUNT = 5;
    const extensions = new Set(["txt", "md", "csv", "pdf", "png", "jpg", "jpeg", "webp"]);
    const sizeLabel = size => size < 1024 ? size + " B" : size < 1024 * 1024
        ? (size / 1024).toFixed(1) + " KiB" : (size / 1024 / 1024).toFixed(1) + " MiB";
    const fileUrl = file => `/conversations/${file.conversation_id}/files/${file.id}`;

    function card(file, parent) {
        const element = document.createElement("div");
        element.className = "attachment-card";
        const type = document.createElement("span");
        type.className = "attachment-type";
        type.textContent = file.original_filename.split(".").at(-1).toUpperCase();
        type.setAttribute("aria-hidden", "true");
        const info = document.createElement("div");
        info.className = "attachment-info";
        const name = document.createElement("strong");
        name.textContent = file.original_filename;
        name.title = file.original_filename;
        const details = document.createElement("span");
        details.textContent = `${file.mime_type || "File"} · ${sizeLabel(file.size)}`;
        info.append(name, details);
        element.append(type, info);
        parent.append(element);
        return element;
    }

    function renderHistory(article, files = []) {
        if (!files.length) return;
        const group = document.createElement("div");
        group.className = "message-attachments";
        group.setAttribute("aria-label", "Message attachments");
        for (const file of files) {
            const element = card(file, group);
            const state = document.createElement("span");
            state.className = "attachment-state";
            if (!file.available) state.textContent = "File unavailable";
            else {
                const download = document.createElement("a");
                download.href = fileUrl(file);
                download.textContent = "Download";
                download.setAttribute("download", file.original_filename);
                download.setAttribute("aria-label", "Download " + file.original_filename);
                download.addEventListener("click", async event => {
                    event.preventDefault();
                    download.setAttribute("aria-disabled", "true");
                    state.textContent = "Preparing download…";
                    try {
                        const response = await fetch(fileUrl(file), { cache: "no-store" });
                        if (!response.ok) throw new Error("File unavailable");
                        const url = URL.createObjectURL(await response.blob());
                        const link = document.createElement("a");
                        link.href = url;
                        link.download = file.original_filename;
                        link.click();
                        setTimeout(() => URL.revokeObjectURL(url), 30000);
                        state.textContent = "";
                    } catch { state.textContent = "File unavailable. Try again or reload this chat."; }
                    finally { download.removeAttribute("aria-disabled"); }
                });
                element.append(download);
                if (file.has_preview) {
                    const preview = document.createElement("img");
                    preview.className = "attachment-preview";
                    preview.alt = "Preview of " + file.original_filename;
                    preview.loading = "lazy";
                    preview.src = fileUrl(file) + "?preview=true";
                    preview.addEventListener("error", () => { preview.hidden = true; state.textContent = "Preview unavailable"; });
                    element.prepend(preview);
                }
            }
            element.append(state);
        }
        article.append(group);
    }

    function attach(options) {
        const picker = document.getElementById("attachmentInput");
        const attachButton = document.getElementById("attachButton");
        const list = document.getElementById("composerAttachments");
        const composer = document.getElementById("messageForm");
        const notice = document.getElementById("attachmentNotice");
        const drafts = new Map();
        let selecting = false;
        const current = () => drafts.get(options.conversationId()) || [];
        const notify = text => { notice.textContent = text; notice.hidden = !text; };
        const changed = () => { render(); options.changed(); };
        const blocked = () => selecting || current().some(item => item.state !== "ready");
        const enabled = () => !options.busy() && !options.editing() && !selecting && !current().some(item => item.id === "loading");

        function render() {
            const focused = list.contains(document.activeElement) ? {
                id: document.activeElement.closest(".attachment-card")?.dataset.fileId,
                label: document.activeElement.getAttribute("aria-label")
            } : null;
            list.replaceChildren();
            list.hidden = !current().length;
            attachButton.disabled = !enabled();
            picker.disabled = !enabled();
            for (const item of current()) {
                const element = card(item.meta || {original_filename: item.file.name, mime_type: item.file.type, size: item.file.size}, list);
                element.dataset.fileId = item.id;
                if (item.meta?.has_preview) {
                    const preview = document.createElement("img");
                    preview.className = "attachment-preview";
                    preview.alt = "Preview of " + item.meta.original_filename;
                    preview.src = fileUrl(item.meta) + "?preview=true";
                    preview.addEventListener("error", () => { preview.hidden = true; });
                    element.prepend(preview);
                }
                const state = document.createElement("span");
                state.className = "attachment-state";
                state.textContent = item.state === "ready" ? "Uploaded" : item.state === "uploading"
                    ? (item.progress === 100 ? "Validating…" : `Uploading ${item.progress}%`) : item.error || "Upload failed";
                state.setAttribute("role", "status");
                element.append(state);
                if (item.id === "loading") {
                    if (item.state === "error") {
                        const reload = document.createElement("button");
                        reload.type = "button";
                        reload.textContent = "Retry loading";
                        reload.addEventListener("click", () => { drafts.delete(item.cid); restore(); });
                        element.append(reload);
                    }
                    continue;
                }
                if (item.state === "uploading") {
                    const progress = document.createElement("progress");
                    progress.max = 100;
                    progress.value = item.progress;
                    progress.setAttribute("aria-label", "Upload progress for " + item.file.name);
                    element.append(progress);
                }
                if (item.state === "error" && item.file && !item.removing) {
                    const retry = document.createElement("button");
                    retry.type = "button";
                    retry.textContent = "Retry";
                    retry.setAttribute("aria-label", "Retry upload of " + item.file.name);
                    retry.disabled = !enabled();
                    retry.addEventListener("click", () => upload(item));
                    element.append(retry);
                }
                const remove = document.createElement("button");
                remove.type = "button";
                remove.textContent = "Remove";
                remove.setAttribute("aria-label", "Remove " + (item.meta?.original_filename || item.file.name));
                remove.disabled = options.busy() || item.state === "removing";
                remove.addEventListener("click", async () => {
                    item.removing = true;
                    item.xhr?.abort();
                    item.state = "removing";
                    item.error = "Removing…";
                    changed();
                    try {
                        await options.json(`/conversations/${item.cid}/files/${item.id}`, "Couldn't remove attachment", {method: "DELETE"});
                        drafts.set(item.cid, (drafts.get(item.cid) || []).filter(other => other !== item));
                    } catch (error) { item.state = "error"; item.error = error.message; }
                    finally { changed(); }
                });
                element.append(remove);
            }
            if (focused?.label) {
                Array.from(list.querySelectorAll("button")).find(control =>
                    control.closest(".attachment-card").dataset.fileId === focused.id &&
                    control.getAttribute("aria-label") === focused.label)?.focus({preventScroll: true});
            }
        }

        function upload(item) {
            item.state = "uploading";
            item.progress = 0;
            item.removing = false;
            const xhr = item.xhr = new XMLHttpRequest();
            xhr.open("POST", `/conversations/${item.cid}/files/${item.id}`);
            xhr.timeout = 120000;
            xhr.upload.addEventListener("progress", event => {
                if (event.lengthComputable) item.progress = Math.round(event.loaded / event.total * 100);
                if (!item.removing) render();
            });
            const failed = message => {
                if (item.removing) return;
                item.state = "error";
                item.error = message;
                changed();
            };
            xhr.addEventListener("load", () => {
                if (item.removing) return;
                let data;
                try { data = JSON.parse(xhr.responseText); } catch { failed("Upload failed. Retry or remove this file."); return; }
                if (xhr.status < 200 || xhr.status >= 300) { failed(typeof data.detail === "string" ? data.detail : "Upload rejected."); return; }
                item.meta = data;
                item.state = data.available ? "ready" : "error";
                if (!data.available) item.error = "File unavailable. Remove and upload again.";
                changed();
            });
            xhr.addEventListener("error", () => failed("Connection lost. Retry this upload."));
            xhr.addEventListener("timeout", () => failed("Upload timed out. Retry this upload."));
            xhr.addEventListener("abort", () => failed("Upload interrupted. Retry this upload."));
            const form = new FormData();
            form.append("file", item.file, item.file.name);
            xhr.send(form);
            changed();
        }

        async function add(files) {
            if (!enabled() || !files.length) return;
            notify("");
            const queued = current().slice();
            const accepted = [];
            let total = queued.reduce((sum, item) => sum + (item.file?.size || item.meta.size), 0);
            for (const file of files) {
                if (!extensions.has(file.name.split(".").at(-1).toLowerCase())) { notify("Unsupported file. Choose TXT, MD, CSV, PDF, PNG, JPEG, or WebP."); continue; }
                if (!file.size || file.size > MAX_FILE) { notify("Each file must be nonempty and at most 10 MiB."); continue; }
                if (queued.length + accepted.length >= MAX_COUNT || total + file.size > MAX_TOTAL) { notify("Choose up to 5 files, totaling at most 25 MiB."); continue; }
                accepted.push(file);
                total += file.size;
            }
            if (!accepted.length) return;
            selecting = true;
            options.lock(true);
            changed();
            try {
                const cid = await options.ensureConversation();
                const entries = drafts.get(cid) || [];
                drafts.set(cid, entries);
                for (const file of accepted) {
                    const item = {id: crypto.randomUUID(), cid, file, state: "uploading", progress: 0};
                    entries.push(item);
                    upload(item);
                }
            } catch (error) { notify(error.message); }
            finally { selecting = false; options.lock(false); changed(); }
        }

        async function restore() {
            const cid = options.conversationId();
            notify("");
            render();
            if (cid === null || drafts.has(cid)) return;
            // Loading pending metadata blocks sends too: a reloaded draft must
            // not accidentally send before its existing uploads are restored.
            const loading = [{state: "loading", id: "loading", cid,
                meta: {original_filename: "Loading attachments", mime_type: "", size: 0}, error: "Loading…"}];
            drafts.set(cid, loading);
            changed();
            try {
                const data = await options.json(`/conversations/${cid}/files`, "Couldn't load draft attachments");
                if (drafts.get(cid) !== loading) return;
                drafts.set(cid, data.files.map(meta => ({id: meta.id, cid, meta,
                    state: meta.available ? "ready" : "error", error: "File unavailable. Remove and upload again."})));
            } catch (error) {
                // Keep Send blocked rather than silently dropping unseen files.
                const item = drafts.get(cid)?.[0];
                if (item?.state === "loading") { item.state = "error"; item.error = error.message + " Reload to retry."; }
            }
            if (options.conversationId() === cid) changed();
        }
        attachButton.addEventListener("click", () => picker.click());
        picker.addEventListener("change", () => { add(Array.from(picker.files)); picker.value = ""; });
        composer.addEventListener("dragover", event => {
            if (Array.from(event.dataTransfer.types).includes("Files")) {
                event.preventDefault();
                event.dataTransfer.dropEffect = enabled() ? "copy" : "none";
                composer.classList.toggle("drag-over", enabled());
            }
        });
        composer.addEventListener("dragleave", event => { if (!composer.contains(event.relatedTarget)) composer.classList.remove("drag-over"); });
        composer.addEventListener("drop", event => {
            event.preventDefault();
            composer.classList.remove("drag-over");
            add(Array.from(event.dataTransfer.files));
        });
        composer.addEventListener("paste", event => {
            const files = Array.from(event.clipboardData?.files || []);
            if (files.length && enabled()) { event.preventDefault(); add(files); }
        });
        // Files dropped outside the composer should not navigate away from chat.
        window.addEventListener("dragover", event => { if (Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault(); });
        window.addEventListener("drop", event => { if (event.dataTransfer.files.length) event.preventDefault(); });
        return {add, restore, render, blocked, hasFiles: () => current().length > 0,
            ids: () => current().filter(item => item.state === "ready").map(item => item.id),
            sent: cid => { drafts.set(cid, []); changed(); }, forget: cid => drafts.delete(cid)};
    }
    return {attach, renderHistory};
})();
