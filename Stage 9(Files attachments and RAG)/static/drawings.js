/* Drawing review is independent of response scrolling and model output. */
(() => {
    const el = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
    async function api(path, body) {
        const response = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (!response.ok) {
            const text=await response.text();let message=text;
            try { message=JSON.parse(text).detail || text; } catch (_) {}
            throw new Error(typeof message==="string"?message:"Drawing request failed; check the selected values.");
        }
        return response.json();
    }
    async function open(body, cid, fid, initial = {}) {
        const base = `/conversations/${cid}/files/${fid}/drawing`;
        const root = el("section"); root.className = "drawing-review"; body.replaceChildren(root);
        const controls = el("div"), status = el("p", "Loading drawing evidence…"), detail = el("pre");
        const page = el("input"); page.type = "number"; page.min = "1"; page.value = String(initial.page || 1); page.setAttribute("aria-label", "PDF page index");
        const zoom = el("input"); zoom.type = "range"; zoom.min = "50"; zoom.max = "300"; zoom.value = "100"; zoom.setAttribute("aria-label", "Drawing zoom");
        const viewport = el("div"); viewport.className = "drawing-viewport"; viewport.tabIndex = 0; viewport.setAttribute("aria-label", "Original drawing, scroll to pan");
        const surface = el("div"); surface.className = "drawing-surface";
        const image = el("img"); image.alt = "Original PDF page"; image.draggable = false;
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("aria-label", "Drawing evidence overlays");
        surface.append(image, svg); viewport.append(surface);
        const tools = el("div"), templates = el("select"); templates.setAttribute("aria-label", "Symbol reference template");
        const label = el("input"); label.placeholder = "Name for selected reference"; label.setAttribute("aria-label", "Reference symbol name");
        const reviewer = el("input"); reviewer.placeholder = "Reviewer name"; reviewer.setAttribute("aria-label", "Human reviewer name");
        const correctionPanel=el("fieldset");correctionPanel.append(el("legend","Object, dimension and connection corrections"));
        const kind=el("select");kind.setAttribute("aria-label","Correction type");
        for(const name of ["symbol","dimension","junction","connection","gap"]){const option=el("option",name);option.value=name;kind.append(option);}
        const correctionLabel=el("input");correctionLabel.placeholder="Object tag, dimension name or connection name";correctionLabel.setAttribute("aria-label","Correction label");
        const objectId=el("input");objectId.placeholder="Dimension's equipment tag";objectId.setAttribute("aria-label","Dimension equipment identity");
        const value=el("input");value.placeholder="Explicit dimension value";value.setAttribute("aria-label","Dimension value");
        const unit=el("select");unit.setAttribute("aria-label","Dimension unit");for(const name of ["mm","cm","m","in","ft"]){const o=el("option",name);o.value=name;unit.append(o);}
        const start=el("select"),end=el("select"),lineType=el("select"),direction=el("select"),correctionList=el("select");
        start.setAttribute("aria-label","Connection start object");end.setAttribute("aria-label","Connection end object");correctionList.setAttribute("aria-label","Saved correction");
        lineType.setAttribute("aria-label","Reviewed line type");direction.setAttribute("aria-label","Reviewed direction");
        for(const name of ["unknown","process","signal","dimension","annotation"]){const o=el("option",name);o.value=name;lineType.append(o);}
        for(const name of ["unknown","undirected","start_to_end"]){const o=el("option",name);o.value=name;direction.append(o);}
        const correctionAttestation=el("input");correctionAttestation.type="checkbox";
        const correctionAttestationLabel=el("label"," I checked these listed corrections against the original source. This does not verify an exhaustive symbol total.");correctionAttestationLabel.prepend(correctionAttestation);
        correctionPanel.append(kind,correctionLabel,objectId,value,unit,start,end,lineType,direction,correctionList,correctionAttestationLabel);
        const referenceImages=el("select");referenceImages.setAttribute("aria-label","Uploaded reference image");
        const blind = el("input"); blind.type = "checkbox"; blind.checked = true;
        const blindLabel = el("label", " Independent review: hide predictions "); blindLabel.prepend(blind);
        const grid = el("fieldset"); grid.append(el("legend", "Mark each region only after inspecting its original symbols"));
        grid.className="count-review-grid";
        const cells = [];
        for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) {
            const item = el("label", `${x + 1},${y + 1} `), check = el("input"); check.type = "checkbox";
            check.dataset.cell = `${x}:${y}`; item.prepend(check); grid.append(item); cells.push(check);
        }
        const independent = el("input"); independent.type = "checkbox";
        const attest = el("label", " I independently reviewed every region against the original drawing, resolved ambiguities, and counted each included symbol once."); attest.prepend(independent);
        const drafts=el("select");drafts.setAttribute("aria-label","Saved symbol review draft");
        const unresolved=el("textarea");unresolved.setAttribute("aria-label","Unresolved review items");unresolved.placeholder="Unresolved items, one per line. Resolve every item before exhaustive publication.";
        let data, revision, templateMap = {}, findings = initial.regions || [], annotations = [], selected = null, first = null, serial = 0;
        let corrections=[];
        function resetReview() {
            annotations=[];cells.forEach(c=>c.checked=false);independent.checked=false;unresolved.value="";selected=null;
        }
        async function refreshDrafts() {
            const result=await api(base+"/reviews");drafts.replaceChildren();
            for(const draft of result.reviews.filter(r=>r.published===false)) {
                const o=el("option",`${draft.reviewer} — ${draft.id.slice(-8)}${draft.saved_revision!==revision?" (stale)":""}`);
                o.value=draft.id;o.disabled=draft.saved_revision!==revision;drafts.append(o);
            }
        }
        const cropPreview=el("div");cropPreview.hidden=true;cropPreview.setAttribute("role","region");cropPreview.setAttribute("aria-label","High resolution original region");
        const regionInputs=["Left","Top","Right","Bottom"].map(name=>{const input=el("input");input.type="number";input.min="0";input.step="any";input.placeholder=name;input.setAttribute("aria-label","Selected region "+name.toLowerCase());return input;});
        function correctionOptions(){
            for(const select of [start,end,correctionList])select.replaceChildren();
            for(const c of corrections){
                const option=el("option",c.label+" ("+c.kind+")");option.value=c.id;correctionList.append(option);
                if(["symbol","junction"].includes(c.kind))for(const select of [start,end]){const o=el("option",c.label);o.value=c.id;select.append(o);}
            }
        }
        function button(text, action, parent = tools) {
            const b = el("button", text); b.type = "button";
            b.addEventListener("click", async () => { b.disabled = true; try { await action(); } catch (error) { status.textContent = error.message; } finally { b.disabled = false; } });
            parent.append(b); return b;
        }
        function drawBox(box, color, caption, action) {
            const rect = document.createElementNS(svg.namespaceURI, "rect");
            for (const [key, value] of Object.entries({x:box[0],y:box[1],width:box[2]-box[0],height:box[3]-box[1],fill:"transparent",stroke:color,"stroke-width":2})) rect.setAttribute(key,String(value));
            const title = document.createElementNS(svg.namespaceURI, "title"); title.textContent = caption; rect.append(title);
            if (action) { rect.tabIndex = 0; rect.setAttribute("role", "button"); rect.setAttribute("aria-label",caption); rect.addEventListener("click",action); rect.addEventListener("keydown",e=>{if(e.key==="Enter")action();}); }
            svg.append(rect);
        }
        function paint() {
            svg.replaceChildren(); if (!data) return;
            if (!blind.checked) for (const f of findings.filter(f=>(f.page || +page.value)===+page.value)) drawBox(f.bbox,"#b16a00",f.label || f.class || f.key || "Automatic finding",()=>{ detail.textContent=JSON.stringify(f,null,2); });
            for (const a of annotations.filter(a=>a.page===+page.value)) drawBox(a.bbox,"#087e9c",a.class,()=>{selected=a.bbox;detail.textContent=JSON.stringify(a,null,2);});
            if(!blind.checked)for(const c of corrections.filter(c=>c.page===+page.value))drawBox(c.bbox,"#663399",c.label,()=>{selected=c.bbox;correctionList.value=c.id;detail.textContent=JSON.stringify(c,null,2);});
            if (selected) drawBox(selected,"#b30075","Selected region");
        }
        function focusRegion(bounds) {
            if(!data || !bounds || bounds.length!==4 || !bounds.every(Number.isFinite) ||
               bounds[0]<0 || bounds[1]<0 || bounds[0]>=bounds[2] || bounds[1]>=bounds[3] ||
               bounds[2]>data.geometry.width || bounds[3]>data.geometry.height)return;
            selected=[...bounds];regionInputs.forEach((input,i)=>input.value=String(bounds[i]));paint();
            requestAnimationFrame(()=>{
                if(!root.isConnected)return;
                viewport.scrollLeft=(bounds[0]+bounds[2])/2/data.geometry.width*surface.clientWidth-viewport.clientWidth/2;
                viewport.scrollTop=(bounds[1]+bounds[3])/2/data.geometry.height*surface.clientHeight-viewport.clientHeight/2;
            });
        }
        function position(event) {
            const r = svg.getBoundingClientRect();
            return [Math.max(0,Math.min(data.geometry.width,(event.clientX-r.left)/r.width*data.geometry.width)),Math.max(0,Math.min(data.geometry.height,(event.clientY-r.top)/r.height*data.geometry.height))];
        }
        svg.addEventListener("pointerdown",event=>{if(!data)return;first=position(event);svg.setPointerCapture(event.pointerId);});
        svg.addEventListener("pointerup",event=>{if(!first)return;const last=position(event);selected=[Math.min(first[0],last[0]),Math.min(first[1],last[1]),Math.max(first[0],last[0]),Math.max(first[1],last[1])];first=null;if(selected[2]-selected[0]<1 || selected[3]-selected[1]<1)selected=null;paint();});
        zoom.addEventListener("input",()=>{surface.style.width=zoom.value+"%";}); blind.addEventListener("change",paint);
        async function load() {
            const token = ++serial; status.textContent = "Reading native text and geometry…";
            const result = await api(base+"/analyze",{page:+page.value}); if(token!==serial || !root.isConnected)return;
            data=result.data;revision=result.revision;page.max=String(data.page_count);
            svg.setAttribute("viewBox",`0 0 ${data.geometry.width} ${data.geometry.height}`);
            image.src=base+"/render?page="+page.value;image.onerror=()=>{status.textContent="The original page is unavailable.";};
            surface.style.aspectRatio=`${data.geometry.width}/${data.geometry.height}`;
            templateMap=(await api(base+"/templates")).templates;templates.replaceChildren();
            for(const [key,t] of Object.entries(templateMap)){const option=el("option",t.name);option.value=key;templates.append(option);}
            const annotationSets=await api(base+"/annotations");corrections=(annotationSets.draft || annotationSets.published)?.objects || [];revision=annotationSets.revision;correctionOptions();
            referenceImages.replaceChildren();const available=await api(`/conversations/${cid}/files`);
            for(const f of available.files || [])if(f.mime_type.startsWith("image/")){const o=el("option",f.original_filename);o.value=f.id;referenceImages.append(o);}
            findings=initial.regions || data.fields.filter(f=>f.value!==null);resetReview();await refreshDrafts();
            status.textContent=`PDF page ${data.page}, label ${data.page_label}. ${data.not_to_scale?"NTS: physical measurement from pixels is unavailable. ":""}Automatic findings are not independently verified.`;paint();
            const cited=(initial.regions || []).find(region=>region.page===data.page);
            const target=cited?.bbox || (Number(initial.page || 1)===data.page?initial.bbox:null);
            if(target)focusRegion(target);
            image.onload=()=>{if(token===serial && target)focusRegion(target);};
        }
        controls.append(page,zoom);button("Load page",load,controls);
        controls.append(...regionInputs);
        button("Select coordinates",()=>{
            const bounds=regionInputs.map(i=>Number(i.value));
            if(regionInputs.some(i=>!i.value) || !bounds.every(Number.isFinite) || bounds[0]<0 || bounds[1]<0 || !(bounds[0]<bounds[2]&&bounds[1]<bounds[3]&&bounds[2]<=data.geometry.width&&bounds[3]<=data.geometry.height))throw new Error("Enter a rectangle within the displayed page coordinates.");
            focusRegion(bounds);
        },controls);
        button("Inspect selected region",()=>{
            if(!selected)throw new Error("Select a region first.");
            const scale=Math.min(4,2048/Math.max(selected[2]-selected[0],selected[3]-selected[1]));
            const image=el("img");image.alt="Original high resolution PDF crop at "+selected.join(", ");image.style.maxWidth="100%";
            image.src=base+"/render?page="+page.value+"&bbox="+encodeURIComponent(JSON.stringify(selected))+"&scale="+scale;
            cropPreview.replaceChildren(el("p","Original crop; page "+page.value+", bounds "+selected.join(", ")),image);cropPreview.hidden=false;
            button("Close crop",()=>{cropPreview.hidden=true;cropPreview.replaceChildren();},cropPreview);
            cropPreview.tabIndex=0;cropPreview.focus();
        },controls);
        tools.append(templates,label,blindLabel);
        templates.addEventListener("change",()=>{resetReview();paint();});
        tools.append(referenceImages);
        button("Use uploaded reference image",async()=>{
            if(!referenceImages.value || !label.value.trim())throw new Error("Select an uploaded reference image and give it a name.");
            const t=await api(base+"/templates/image",{source_hash:data.source_hash,expected_revision:revision,reference_file_id:referenceImages.value,name:label.value.trim()});
            templateMap[t.id]=t;const option=el("option",t.name);option.value=t.id;templates.append(option);templates.value=t.id;revision=t.revision;
            status.textContent="Reference image saved. Matches do not establish an engineering class.";
        });
        button("Use selected region as reference",async()=>{
            if(!selected || !label.value.trim())throw new Error("Select a region and give the reference a name.");
            const t=await api(base+"/templates",{page:+page.value,bbox:selected,name:label.value.trim(),source_hash:data.source_hash,expected_revision:revision});
            templateMap[t.id]=t;const option=el("option",t.name);option.value=t.id;templates.append(option);templates.value=t.id;revision=t.revision;
            status.textContent="Reference saved. Its engineering class remains a reviewer assertion.";
        });
        button("Find matches on this page",async()=>{
            status.textContent="Matching supported scales and rotations; a budget limit produces Partial coverage…";
            const result=await api(base+"/count",{pages:[+page.value],template_id:templates.value});
            findings=result.detections;blind.checked=false;status.textContent=`${result.label}: ${result.count} matches. ${result.limitations.join(" ")}`;detail.textContent=JSON.stringify(result.scope,null,2);paint();
        });
        button("Add manual symbol",()=>{
            if(!selected)throw new Error("Select the symbol's bounding box on the original page.");
            annotations.push({id:crypto.randomUUID(),page:+page.value,bbox:[...selected],class:templateMap[templates.value].name});selected=null;paint();
        });
        button("Remove selected annotation",()=>{annotations=annotations.filter(a=>JSON.stringify(a.bbox)!==JSON.stringify(selected));selected=null;paint();});
        button("Add correction in selected region",()=>{
            if(!selected || !correctionLabel.value.trim())throw new Error("Select the original region and enter a label.");
            const inside=b=>selected[0]<=b[0]&&selected[1]<=b[1]&&selected[2]>=b[2]&&selected[3]>=b[3];
            const sourceIds=[...data.words,...data.paths].filter(p=>inside(p.bbox)).map(p=>p.id).slice(0,200);
            if(!sourceIds.length)throw new Error("The region must contain original native text or paths supporting the correction.");
            corrections.push({id:crypto.randomUUID(),kind:kind.value,page:+page.value,bbox:[...selected],label:correctionLabel.value.trim(),source_ids:sourceIds,
                object_id:kind.value==="dimension"?objectId.value.trim():null,value:kind.value==="dimension"?value.value.trim():null,unit:kind.value==="dimension"?unit.value:null,
                start:kind.value==="connection"?start.value:null,end:kind.value==="connection"?end.value:null,line_type:lineType.value,direction:direction.value});
            correctionOptions();selected=null;blind.checked=false;paint();
        },correctionPanel);
        button("Remove correction",()=>{corrections=corrections.filter(c=>c.id!==correctionList.value);correctionOptions();paint();},correctionPanel);
        async function saveCorrections(publish){
            const result=await api(base+"/annotations",{source_hash:data.source_hash,expected_revision:revision,reviewer:reviewer.value,objects:corrections,publish,attestation:correctionAttestation.checked});
            revision=result.revision;status.textContent=publish?"Published human-reviewed corrections. This is not exhaustive count verification.":"Correction draft saved.";
        }
        button("Save correction draft",()=>saveCorrections(false),correctionPanel);
        button("Publish listed corrections",()=>saveCorrections(true),correctionPanel);
        function reviewBody() {
            if(data.page!==+page.value)throw new Error("Load the selected page before saving its review.");
            return {source_hash:data.source_hash,expected_revision:revision,reviewer:reviewer.value,
                scope:{file_id:fid,source_hash:data.source_hash,pages:[+page.value],bbox:null,template_id:templates.value,template_version:templateMap[templates.value].version},
                objects:annotations,reviewed_cells:cells.filter(c=>c.checked).map(c=>`${page.value}:${c.dataset.cell}`),
                independent:independent.checked,exhaustive:independent.checked,unresolved:unresolved.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean)};
        }
        tools.append(drafts);
        button("Restore review draft",async()=>{
            if(!drafts.value)throw new Error("No current review draft is available.");
            const result=await api(base+"/reviews/"+encodeURIComponent(drafts.value)),draft=result.data;
            if(draft.scope.pages.length!==1 || draft.scope.pages[0]!==data.page || draft.scope.bbox!==null)
                throw new Error("Load the draft's page. This viewer restores full-page reviews only.");
            if(!templateMap[draft.scope.template_id] || templateMap[draft.scope.template_id].version!==draft.scope.template_version)
                throw new Error("Reload the drawing to inspect the changed template.");
            resetReview();revision=result.revision;templates.value=draft.scope.template_id;reviewer.value=draft.reviewer;
            annotations=draft.objects;unresolved.value=draft.unresolved.join("\n");
            cells.forEach(c=>c.checked=draft.reviewed_cells.includes(`${data.page}:${c.dataset.cell}`));
            blind.checked=true;paint();status.textContent="Review draft restored. Check remaining regions and attest again before publication.";
        });
        button("Save review draft",async()=>{const result=await api(base+"/reviews/draft",reviewBody());revision=result.revision;await refreshDrafts();status.textContent="Draft saved; it is not verified ground truth.";});
        button("Publish exhaustive human review",async()=>{
            const result=await api(base+"/reviews/publish",reviewBody());revision=result.revision;status.textContent="Recorded human review: "+result.verification_id;detail.textContent=JSON.stringify(result,null,2);
        });
        root.append(controls,status,viewport,cropPreview,tools,reviewer,correctionPanel,grid,unresolved,attest,detail);
        try { await load(); } catch(error) { status.textContent=error.message; }
    }
    window.DrawingViewer={open};
})();
