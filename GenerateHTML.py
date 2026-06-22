import os
import json
import re
import sys

# Configuration
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
VIS_DIR = os.path.join(PROJECT_ROOT, 'visualisations')
JS_DIR = os.path.join(VIS_DIR, 'js')
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'standalone_output')

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def strip_imports_exports(content):
    # Remove import statements (handling optional whitespace)
    content = re.sub(r'^\s*import .*?;', '', content, flags=re.MULTILINE)
    
    # Remove export default class/function
    content = re.sub(r'export\s+default\s+class', 'class', content)
    content = re.sub(r'export\s+default\s+function', 'function', content)
    
    # Remove named exports
    content = re.sub(r'export\s+class', 'class', content)
    content = re.sub(r'export\s+function', 'function', content)
    content = re.sub(r'export\s+const', 'const', content)
    content = re.sub(r'export\s+let', 'let', content)
    content = re.sub(r'export\s+var', 'var', content)
    
    # Remove export { ... }
    content = re.sub(r'export\s*\{.*?\};', '', content, flags=re.DOTALL)
    
    return content

def bundle_worker():
    # Worker needs utils.js and dataWorker.js
    utils_content = read_file(os.path.join(JS_DIR, 'utils.js'))
    worker_content = read_file(os.path.join(JS_DIR, 'dataWorker.js'))
    
    # Strip imports/exports
    utils_content = strip_imports_exports(utils_content)
    worker_content = strip_imports_exports(worker_content)
    
    combined_worker = f"""
    // --- Embedded utils.js ---
    {utils_content}
    
    // --- Embedded dataWorker.js ---
    {worker_content}
    """
    
    return combined_worker

def bundle_main_js():
    # Order matters for dependencies
    files = [
        'utils.js',
        'TopicLandscapeView.js',
        'CitationNetworkView.js',
        'EgoNetworkView.js',
        'visualisation.js',
        'main.js'
    ]
    
    combined_js = ""
    for filename in files:
        content = read_file(os.path.join(JS_DIR, filename))
        content = strip_imports_exports(content)
        combined_js += f"\n// --- {filename} ---\n{content}\n"
        
    return combined_js

def generate_standalone(docset_hash, output_folder=None):
    print(f"Generating standalone visualization for docset: {docset_hash}")
    
    # 1. Load Data
    try:
        docset_path = os.path.join(DATA_DIR, docset_hash, f"{docset_hash}_docset.json")
        topics_path = os.path.join(DATA_DIR, docset_hash, f"{docset_hash}_topics.json")
        
        docset_data = read_file(docset_path)
        topics_data = read_file(topics_path)
        
        # Get docset name from index.json
        index_path = os.path.join(DATA_DIR, 'index.json')
        docset_name = docset_hash
        if os.path.exists(index_path):
            try:
                index_data = json.loads(read_file(index_path))
                for entry in index_data:
                    if entry.get('hash') == docset_hash:
                        docset_name = entry.get('name', docset_hash)
                        break
            except:
                pass
                
    except FileNotFoundError as e:
        print(f"Error: Could not find data files for {docset_hash}. {e}")
        return

    # 2. Prepare HTML Template
    html_content = read_file(os.path.join(VIS_DIR, 'index.html'))
    
    # 3. Embed CSS
    css_content = read_file(os.path.join(VIS_DIR, 'style.css'))
    html_content = html_content.replace('<link rel="stylesheet" href="style.css">', f'<style>{css_content}</style>')
    
    # 4. Remove module script tag
    html_content = html_content.replace('<script type="module" src="js/main.js"></script>', '')
    
    # 5. Prepare JS Content
    worker_code = bundle_worker()
    main_js_code = bundle_main_js()
    
    # 6. Patch JS Code
    
    # Patch Worker Creation
    # We need to create a Blob URL for the worker
    worker_blob_setup = f"""
    const workerCode = `{worker_code.replace('`', '\\`').replace('${', '\\${')}`;
    const workerBlob = new Blob([workerCode], {{ type: 'application/javascript' }});
    const workerUrl = URL.createObjectURL(workerBlob);
    """
    
    # Replace the worker instantiation in visualisation.js (which is now in main_js_code)
    main_js_code = main_js_code.replace(
        "new Worker('./js/dataWorker.js', { type: 'module' })", 
        "new Worker(workerUrl)"
    )
    
    # Patch Data Loading
    # We inject the data as global variables
    data_injection = f"""
    window.EMBEDDED_DOCSET_NAME = {json.dumps(docset_name)};
    window.EMBEDDED_DOCSET_DATA = {docset_data};
    window.EMBEDDED_TOPICS_DATA = {topics_data};
    """
    
    # Override loadDocsetIndex
    override_logic = """
    Visualization.prototype.loadDocsetIndex = function() {
        d3.select("#current-docset-label").text(window.EMBEDDED_DOCSET_NAME);
        this._processAndDraw(window.EMBEDDED_DOCSET_DATA, window.EMBEDDED_TOPICS_DATA);
        
        // Setup View Mode Selector (copied from original)
        d3.select("#view-mode-selector").on("change", (event) => {
            this.currentViewMode = d3.select(event.currentTarget).property("value");
            this.citationDetailView = false;
            this._clearDetailPositions();
            this.svg.call(this.zoom.transform, d3.zoomIdentity);
            this.update();
        });

        // Setup other global controls (copied from original)
        d3.select("#back-to-abstract-btn").on("click", () => {
            this.citationDetailView = false;
            this.selectedPapers.clear();
            this.selectedClusterKeys.clear();
            d3.select("#show-all-papers-toggle").property("checked", false);
            this.update();
        });
        d3.select("#back-to-global-view-btn").on("click", () => {
            this.currentViewMode = this.previousViewMode || 'topic-landscape';
            this.activeEgoPaper = null;
            this.previousViewMode = null;
            this.svg.call(this.zoom.transform, d3.zoomIdentity);
            this.update();
        });
    };
    """
    
    # 7. Assemble Final HTML
    final_script = f"""
    <script>
    {worker_blob_setup}
    {data_injection}
    
    {main_js_code}
    
    {override_logic}
    </script>
    """
    
    # Inject script before </body>
    final_html = html_content.replace('</body>', f'{final_script}</body>')
    
    # 8. Write Output
    output_filename = f"standalone_{docset_hash}.html"
    
    if output_folder:
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        output_path = os.path.join(output_folder, output_filename)
    else:
        output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_html)
        
    print(f"Success! Standalone file created at: {output_path}")

def generate_all_standalones():
    print("Generating standalone HTML for all docsets in data folder...")
    if not os.path.exists(DATA_DIR):
        print(f"Data directory not found: {DATA_DIR}")
        return

    count = 0
    for entry in os.listdir(DATA_DIR):
        entry_path = os.path.join(DATA_DIR, entry)
        if os.path.isdir(entry_path):
            # Check for docset file to confirm it's a docset folder
            expected_docset_file = os.path.join(entry_path, f"{entry}_docset.json")
            if os.path.exists(expected_docset_file):
                generate_standalone(entry, output_folder=entry_path)
                count += 1
    
    print(f"Finished! Generated {count} standalone files.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # If no arguments provided, generate for all docsets
        generate_all_standalones()
    else:
        # If argument provided, generate for that specific docset
        docset_hash = sys.argv[1]
        docset_folder = os.path.join(DATA_DIR, docset_hash)
        if os.path.exists(docset_folder) and os.path.isdir(docset_folder):
             generate_standalone(docset_hash, output_folder=docset_folder)
        else:
             # Fallback to default output dir if folder structure doesn't match
             generate_standalone(docset_hash)
