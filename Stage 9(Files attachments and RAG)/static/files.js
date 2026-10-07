/* Files are owned by SQLite. This module keeps only pending upload UI state. */
window.FileUI = (() => {
    let hooks, cid = null, files = [], pending = [], selected = new Set(), enabled = false;
    let generation = 0, timer, loading = false, busy = false, debug = false, visionModel = null;
    const panel = document.getElementById("filesPanel");
    const list = document.getElementById("documentList");
    const chips = document.getElementById("attachmentChips");
    const picker = document.getElementById("filePicker");
    const useFiles = document.getElementById("useFiles");
    const message = document.getElementById("filesStatus");
    const preview = document.getElementById("filePreviewDialog");
    const savedSelections = new Map();
    const sizes = bytes => bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + " MiB" : Math.ceil(bytes / 1024) + " KiB";
    const node = (tag, text, className) => {
        const el = document.createElement(tag);
        if (text !== undefined) el.textContent = text;
        if (className) el.className = className;
        return el;
    };
    async function api(url, options = {}) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(url, { cache: "no-store", ...options, signal: controller.signal });
            if (!response.ok) {
                let detail;
                try { detail = (await response.json()).detail; } catch {}
                throw new Error(typeof detail === "string" ? detail : "File request failed (HTTP " + response.status + ").");
            }
            return await response.json();
        } catch (error) {
            if (error.name === "AbortError") throw new Error("The file request timed out. Check the server, then retry.");
            throw error;
        } finally { clearTimeout(timer); }
    }
    const url = (id = cid) => "/conversations/" + id + "/files";
    function control(label, action) {
        const el = node("button", label);
        el.type = "button";
        el.disabled = busy;
        el.addEventListener("click", () => {
            if (!busy) Promise.resolve().then(action).catch(error => hooks.notice(error.message));
        });
        return el;
    }
    function blocked() {
        return loading || pending.some(p => p.state === "uploading" || p.state === "failed") ||
            (enabled && (!selected.size || [...selected].some(id => !files.find(f => f.id === id)?.capabilities?.tools)));
    }
    function render() {
        list.replaceChildren();
        chips.replaceChildren();
        useFiles.checked = enabled;
        useFiles.disabled = busy || loading;
        document.getElementById("attachButton").disabled = busy || loading || pending.some(p => p.state === "uploading");
        picker.disabled = busy || loading;
        message.textContent = loading ? "Loading files..." : !files.length ? "Attach a document to search it. Images can be read when an optional vision model is configured." :
            "Select extracted documents, then enable Use files. Tools and search use only this chat.";
        for (const item of pending) {
            const chip = node("div", undefined, "attachment-chip");
            chip.append(node("span", item.name + " · " + sizes(item.size)));
            chip.append(node("small", item.state === "uploading" ? "Uploading " + item.progress + "%" :
                item.state === "failed" ? item.error : "Uploaded"));
            if (item.state === "uploading") {
                const progress = node("progress");
                progress.max = 100; progress.value = item.progress;
                progress.setAttribute("aria-label", "Upload progress for " + item.name);
                chip.append(progress);
            }
            if (item.state === "failed") chip.append(control("Retry upload", async () => {
                hooks.setOperation("uploading");
                try { await upload(item); } finally { hooks.setOperation(""); }
            }));
            chip.append(control("Remove " + item.name, () => {
                item.xhr?.abort();
                pending = pending.filter(p => p !== item);
                selected.delete(item.id);
                render();
            }));
            chips.append(chip);
        }
        for (const file of files) {
            const row = node("div", undefined, "document-row");
            const label = node("label");
            const check = node("input"); check.type = "checkbox";
            check.checked = selected.has(file.id);
            check.disabled = busy || !file.capabilities?.tools || !file.available;
            check.addEventListener("change", () => {
                if (check.checked) selected.add(file.id); else selected.delete(file.id);
                render();
            });
            label.append(check, node("span", file.original_filename));
            row.append(label, node("small", file.mime_type + " · " + sizes(file.size) + " · " + file.state));
            if (file.error || !file.available) row.append(node("p", file.available ? file.error : "Stored file unavailable.", "file-error"));
            const actions = node("div", undefined, "file-actions");
            actions.append(control("Preview", () => showFile(file)));
            const download = node("a", "Download");
            download.href = url() + "/" + file.id + "/download"; download.download = file.original_filename;
            actions.append(download);
            if (!file.mime_type.startsWith("image/") || visionModel) actions.append(control("Re-index", async () => {
                await api(url()+"/"+file.id+"/reindex", {method:"POST"});
                hooks.notice("Indexing requested. Unchanged files with a compatible index can be reused.");
                await refresh();
            }));
            actions.append(control("Replace", () => {
                picker.dataset.replace = file.id;
                picker.multiple = false;
                picker.click();
            }));
            actions.append(control("Delete", async () => {
                if (!window.confirm('Delete "' + file.original_filename + '" and its indexed passages? Existing answer text remains.')) return;
                await api(url()+"/"+file.id, {method:"DELETE"});
                pending = pending.filter(p => p.id !== file.id);
                selected.delete(file.id);
                await refresh();
                await hooks.reloadMessages();
            }));
            row.append(actions); list.append(row);
        }
        hooks?.changed();
    }
    async function refresh() {
        clearTimeout(timer);
        const mine = generation, target = cid;
        if (target === null) { files = []; render(); return; }
        try {
            const result = await api(url(target));
            if (mine !== generation) return;
            files = result.files;
            selected = new Set([...selected].filter(id => files.some(f => f.id === id)));
            // On first visit, select ready documents; the Use files toggle remains explicit.
            render();
        } catch (error) {
            if (mine === generation) hooks.notice(error.message);
        } finally {
            if (mine === generation && cid !== null) timer = setTimeout(refresh,
                files.some(f => ["uploaded","extracting","chunking","embedding","vision"].includes(f.state)) ? 1200 : 6000);
        }
    }
    async function sync() {
        const next = hooks.getId();
        if (next === cid) return;
        savedSelections.set(cid, {selected, enabled, pending});
        ++generation;
        cid = next;
        const saved = savedSelections.get(cid);
        selected = new Set(saved?.selected || []);
        enabled = saved?.enabled || false;
        pending = saved?.pending || [];
        files = [];
        loading = cid !== null;
        render();
        try { await refresh(); }
        finally { if (next === cid) { loading = false; render(); } }
    }
    async function addFiles(incoming, replaceId = null) {
        if (busy || loading) return;
        const items = Array.from(incoming);
        if (pending.length + items.length > 4 || pending.reduce((n,p)=>n+p.size,0)+items.reduce((n,f)=>n+f.size,0)>20*1048576) {
            hooks.notice("Choose at most four files, totaling at most 20 MiB."); return;
        }
        if (items.some(f => f.size > 10*1048576 || !/\.(txt|md|csv|xlsx|pdf|docx|png|jpe?g|webp)$/i.test(f.name))) {
            hooks.notice("Use TXT, MD, CSV, XLSX, PDF, DOCX, PNG, JPEG or WebP, up to 10 MiB per file."); return;
        }
        loading = true; render();
        try {
            await hooks.ensureConversation();
            await sync();
            hooks.setOperation("uploading");
            panel.open = true;
            for (const file of items) {
                const item = {file, name:file.name, size:file.size, state:"uploading", progress:0, cid, replaceId};
                pending.push(item);
                await upload(item);
            }
            if (replaceId) await hooks.reloadMessages();
        } catch (error) { hooks.notice(error.message); }
        finally { loading = false; hooks.setOperation(""); render(); }
    }
    async function upload(item) {
        item.state = "uploading"; item.progress = 0; render();
        try {
            const data = await new Promise((resolve,reject) => {
                const xhr = item.xhr = new XMLHttpRequest();
                xhr.open(item.replaceId ? "PUT" : "POST",url(item.cid)+(item.replaceId ? "/"+item.replaceId : "")); xhr.responseType = "json"; xhr.timeout = 60000;
                xhr.upload.onprogress = event => { if (event.lengthComputable) item.progress = Math.round(100*event.loaded/event.total); render(); };
                xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve(xhr.response) :
                    reject(new Error(xhr.response?.detail || "Upload failed (HTTP "+xhr.status+")."));
                xhr.onerror = () => reject(new Error("Upload failed. Check the connection and retry."));
                xhr.ontimeout = () => reject(new Error("Upload timed out. Retry the same file safely."));
                xhr.onabort = () => reject(new Error("Upload cancelled."));
                const form = new FormData(); form.append("upload",item.file); xhr.send(form);
            });
            item.id = data.file.id; item.state = "uploaded";
            if (item.replaceId) selected.delete(item.replaceId);
            if (data.file.state !== "preview" || visionModel) {
                selected.add(item.id); enabled = true;
            }
            if (item.cid === cid) await refresh();
        } catch (error) { item.state = "failed"; item.error = error.message; }
        finally { item.xhr = null; render(); }
    }
    function openPreview(title) {
        document.getElementById("filePreviewTitle").textContent = title;
        const body = document.getElementById("filePreviewBody"); body.replaceChildren();
        if (!preview.open) preview.showModal();
        return body;
    }
    async function showFile(file) {
        const target = cid, body = openPreview(file.original_filename);
        if (file.mime_type.startsWith("image/")) {
            const image = node("img"); image.alt = file.original_filename;
            image.src = url(target)+"/"+file.id+"/download?preview=true";
            image.onerror = () => body.replaceChildren(node("p","The image is unavailable."));
            body.append(image); return;
        }
        const load = async offset => {
            const data = await api(url(target)+"/"+file.id+"/preview?offset="+offset);
            for (const section of data.sections) {
                body.append(node("h3",section.section || "Document"),node("pre",section.text));
            }
            if (!data.sections.length) body.append(node("p",file.error || "Text extraction is not ready."));
            if (data.next !== null) {
                const more = control("More text",async () => { more.remove(); await load(data.next); });
                body.append(more);
            }
        };
        await load(0);
    }
    async function showSource(source, target) {
        const body = openPreview(source.original_filename);
        try {
            const data = await api("/conversations/"+target+"/sources/"+source.id);
            body.append(node("p",data.metadata.section || "Document passage"),node("pre",data.text));
        } catch (error) { body.append(node("p",error.message)); }
    }
    function decorate(article, msg) {
        article.querySelectorAll(".historical-files, .source-list").forEach(el=>el.remove());
        const target = hooks.getId();
        if (msg.attachments?.length) {
            const container = node("div",undefined,"historical-files");
            for (const file of msg.attachments) {
                const link = node("a",file.original_filename+" · "+sizes(file.size));
                link.href = url(target)+"/"+file.id+"/download";
                if (!file.available) link.textContent += " (unavailable)";
                container.append(link);
                if (file.mime_type.startsWith("image/") && file.available) {
                    const image=node("img"); image.alt=file.original_filename; image.loading="lazy";
                    image.src=link.href+"?preview=true";
                    container.append(image);
                }
            }
            article.append(container);
        }
        if (msg.role === "assistant" && /\[SOURCE_/.test(msg.content)) {
            const sources = msg.sources || [];
            const content = article.querySelector(".message-content");
            const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
            const texts = [];
            while (walker.nextNode()) {
                if (!walker.currentNode.parentElement.closest("pre,code,button")) texts.push(walker.currentNode);
            }
            for (const text of texts) {
                const pattern = /\[(SOURCE_[a-f0-9]{24})\]/g;
                let match, offset = 0;
                const fragment = document.createDocumentFragment();
                while ((match = pattern.exec(text.textContent))) {
                    const index = sources.findIndex(s=>s.id===match[1]);

                    fragment.append(document.createTextNode(text.textContent.slice(offset,match.index)));
                    const source = sources[index] || {id:match[1],original_filename:"Source no longer available"};
                    const cite = node("button",index < 0 ? "[source unavailable]" : "["+String(index+1)+"]","inline-citation");
                    cite.type="button"; cite.title=source.original_filename;
                    cite.setAttribute("aria-label","Open source: "+source.original_filename);
                    cite.addEventListener("click",()=>showSource(source,target));
                    fragment.append(cite);
                    offset=pattern.lastIndex;
                }
                if (offset) { fragment.append(document.createTextNode(text.textContent.slice(offset))); text.replaceWith(fragment); }
            }
            const container=node("div",undefined,"source-list");
            container.append(node("span","Sources: "));
            for (const source of sources) {
                const button=node("button",source.original_filename+" · "+(JSON.parse(source.metadata).section || "passage"));
                button.type="button"; button.title=source.id;
                button.addEventListener("click",()=>showSource(source,target));
                container.append(button);
            }
            article.append(container);
        }
        if (msg.tool_evidence?.length) {
            const container=node("div",undefined,"source-list");
            for (const evidence of msg.tool_evidence) {
                const button=node("button","Tool evidence "+String(evidence.ordinal+1));
                button.type="button";
                button.addEventListener("click",async()=>{
                    const body=openPreview("Deterministic tool evidence");
                    try {
                        const data=await api("/conversations/"+target+"/tool-evidence/"+evidence.id);
                        body.append(node("h3",data.filename),node("pre",JSON.stringify(data,null,2)));
                    } catch(error) { body.append(node("p",error.message)); }
                });
                container.append(button);
            }
            article.append(container);
        }
        if (debug && msg.role === "assistant") {
            const button=node("button","Retrieval details","source-list"); button.type="button";
            button.addEventListener("click",async()=>{
                const body=openPreview("Retrieval diagnostics");
                try { body.append(node("pre",JSON.stringify(await api("/conversations/"+target+"/messages/"+msg.id+"/retrieval"),null,2))); }
                catch(error) { body.append(node("p",error.message)); }
            });
            article.append(button);
        }
    }
    function init(options) {
        hooks=options;
        api("/files/config").then(config=>{debug=config.debug;visionModel=config.vision_model;render();}).catch(error=>hooks.notice(error.message));
        document.getElementById("attachButton").addEventListener("click",()=>{delete picker.dataset.replace;picker.multiple=true;picker.click();});
        picker.addEventListener("change",()=>{addFiles(picker.files,picker.dataset.replace || null);picker.value="";delete picker.dataset.replace;});
        useFiles.addEventListener("change",()=>{enabled=useFiles.checked;render();});
        document.getElementById("closeFilePreview").addEventListener("click",()=>preview.close());
        const composer=document.getElementById("messageForm");
        composer.addEventListener("dragover",e=>{if(e.dataTransfer.types.includes("Files")){e.preventDefault();composer.classList.add("file-drag");}});
        composer.addEventListener("dragleave",()=>composer.classList.remove("file-drag"));
        composer.addEventListener("drop",e=>{e.preventDefault();composer.classList.remove("file-drag");addFiles(e.dataTransfer.files);});
        document.getElementById("messageInput").addEventListener("paste",e=>{
            if(e.clipboardData.files.length){e.preventDefault();addFiles(e.clipboardData.files);}
        });
        render();
    }
    return {init,sync,refresh,decorate,blocked,
        hasAttachments:()=>pending.some(p=>p.state==="uploaded"),
        payload:()=>({attachment_ids:[...new Set(pending.filter(p=>p.state==="uploaded").map(p=>p.id))],
            use_files:enabled,file_ids:enabled?[...selected]:[]}),
        accepted:()=>{pending=[];render();},
        setBusy:value=>{busy=value; if(hooks){sync();render();}},
        addFiles};
})();
