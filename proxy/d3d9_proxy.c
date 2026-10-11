/* daoc-gfx d3d9.dll: forced anisotropic filtering for the DAoC client.
 *
 * Loaded from the client folder in place of the system d3d9.dll (the
 * launcher's WINEDLLOVERRIDES carries d3d9=n,b while the `anisotropy` patch is
 * on). It loads the real d3d9.dll from the system directory (mtld3d under
 * OfflineDAoC-macOS), hands every export through, and changes only how the
 * game's sampler states reach the device: see sampler.h for the rule.
 *
 * Hooking: each IDirect3D9, IDirect3DDevice9 and IDirect3DStateBlock9 object
 * the game gets is given a private copy of its implementation's vtable with a
 * few slots replaced. The object itself is the implementation's, so every
 * other call goes straight to it and identity checks see the same pointers.
 * The copy is freed when the object's last reference goes.
 *
 * Writes `daoc-gfx-d3d9.log` next to this DLL (one line per event, not per
 * call).
 */
#define COBJMACROS
#define INITGUID
#include <initguid.h>
#include <windows.h>
#include <d3d9.h>
#include <stddef.h>

#include "sampler.h"

#ifndef SOURCE_HASH
#define SOURCE_HASH "unknown"
#endif

/* Recognised by daoc-gfx (and by `strings`) as this proxy and its source. */
__attribute__((used)) static const char marker[] = "daoc-gfx d3d9 anisotropy proxy, source " SOURCE_HASH;

#define ANISOTROPY 16

#define SLOT(vtbl, method) (offsetof(vtbl, method) / sizeof(void *))
#define SLOTS(vtbl) (sizeof(vtbl) / sizeof(void *))

static HMODULE self_module;
static HMODULE real_module;
static CRITICAL_SECTION init_lock;
static HANDLE log_file = INVALID_HANDLE_VALUE;

/* ------------------------------------------------------------------ log */

static void log_line(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    DWORD n, written;
    if (log_file == INVALID_HANDLE_VALUE)
        return;
    va_start(ap, fmt);
    n = (DWORD)wvsprintfA(buf, fmt, ap);
    va_end(ap);
    if (n > sizeof(buf) - 3)
        n = sizeof(buf) - 3;
    buf[n++] = '\r';
    buf[n++] = '\n';
    WriteFile(log_file, buf, n, &written, NULL);
}

static void open_log(void)
{
    WCHAR path[MAX_PATH];
    DWORD n = GetModuleFileNameW(self_module, path, MAX_PATH);
    WCHAR *slash;
    if (!n || n >= MAX_PATH)
        return;
    slash = wcsrchr(path, L'\\');
    if (!slash || (slash - path) + 20 >= MAX_PATH)
        return;
    lstrcpyW(slash + 1, L"daoc-gfx-d3d9.log");
    log_file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
}

/* ------------------------------------------------------- the real d3d9 */

/* The system directory's d3d9.dll; never this module again. */
static HMODULE real(void)
{
    EnterCriticalSection(&init_lock);
    if (!real_module) {
        char path[MAX_PATH];
        UINT n = GetSystemDirectoryA(path, MAX_PATH);
        if (n && n + 10 < MAX_PATH) {
            lstrcatA(path, "\\d3d9.dll");
            HMODULE m = LoadLibraryA(path);
            if (m == self_module) {
                log_line("error: %s resolved to this proxy; not forwarding", path);
                FreeLibrary(m);
            } else if (!m) {
                log_line("error: cannot load %s (error %lu)", path, GetLastError());
            } else {
                char loaded[MAX_PATH];
                if (!GetModuleFileNameA(m, loaded, MAX_PATH))
                    lstrcpyA(loaded, "?");
                log_line("real d3d9: %s", loaded);
                real_module = m;
            }
        }
    }
    LeaveCriticalSection(&init_lock);
    return real_module;
}

static FARPROC real_proc(const char *name)
{
    HMODULE m = real();
    FARPROC p = m ? GetProcAddress(m, name) : NULL;
    if (m && !p)
        log_line("error: the real d3d9 has no %s", name);
    return p;
}

/* ------------------------------------------------------------- objects */

typedef struct {
    void *vtbl[SLOTS(IDirect3DDevice9ExVtbl)]; /* first: the object's lpVtbl points here */
    void **orig;
    IDirect3DDevice9 *self;
    gfx_stage st[GFX_STAGES];
    int recording;
    unsigned long writes, applies, blocks;
    int forced_once, game_aniso_logged;
} dev_rec;

typedef struct {
    void *vtbl[SLOTS(IDirect3D9ExVtbl)];
    void **orig;
} d3d_rec;

typedef struct {
    void *vtbl[SLOTS(IDirect3DStateBlock9Vtbl)];
    void **orig;
    dev_rec *dev;
} block_rec;

/* The record behind a hooked object: its lpVtbl is the record's vtbl. */
#define REC(type, obj) ((type *)(*(void ***)(obj)))

/* Copy `n` slots of `obj`'s vtable into `rec->vtbl` and point `obj` at it. */
#define ADOPT(rec, obj, n)                                         \
    do {                                                           \
        (rec)->orig = *(void ***)(obj);                            \
        memcpy((rec)->vtbl, (rec)->orig, (n) * sizeof(void *));    \
        *(void ***)(obj) = (rec)->vtbl;                            \
    } while (0)

/* Whether `obj` answers `iid` with itself, so it carries the larger vtable. */
static int is_same_object(IUnknown *obj, REFIID iid)
{
    IUnknown *other = NULL;
    int same;
    if (FAILED(obj->lpVtbl->QueryInterface(obj, iid, (void **)&other)) || !other)
        return 0;
    same = other == obj;
    other->lpVtbl->Release(other);
    return same;
}

static void *alloc_rec(size_t size)
{
    return HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, size);
}

static void free_rec(void *rec)
{
    HeapFree(GetProcessHeap(), 0, rec);
}

/* -------------------------------------------------------- device hooks */

typedef HRESULT(WINAPI *set_ss_fn)(IDirect3DDevice9 *, DWORD, D3DSAMPLERSTATETYPE, DWORD);
typedef HRESULT(WINAPI *get_ss_fn)(IDirect3DDevice9 *, DWORD, D3DSAMPLERSTATETYPE, DWORD *);
typedef HRESULT(WINAPI *set_tex_fn)(IDirect3DDevice9 *, DWORD, IDirect3DBaseTexture9 *);
typedef HRESULT(WINAPI *reset_fn)(IDirect3DDevice9 *, D3DPRESENT_PARAMETERS *);
typedef HRESULT(WINAPI *reset_ex_fn)(IDirect3DDevice9Ex *, D3DPRESENT_PARAMETERS *, D3DDISPLAYMODEEX *);
typedef HRESULT(WINAPI *create_sb_fn)(IDirect3DDevice9 *, D3DSTATEBLOCKTYPE, IDirect3DStateBlock9 **);
typedef HRESULT(WINAPI *begin_sb_fn)(IDirect3DDevice9 *);
typedef HRESULT(WINAPI *end_sb_fn)(IDirect3DDevice9 *, IDirect3DStateBlock9 **);
typedef ULONG(WINAPI *release_fn)(IUnknown *);
typedef HRESULT(WINAPI *apply_fn)(IDirect3DStateBlock9 *);

#define DEV_SLOT(m) SLOT(IDirect3DDevice9Vtbl, m)
#define DEV_ORIG(d, type, m) ((type)(d)->orig[DEV_SLOT(m)])

static HRESULT real_set(dev_rec *d, DWORD s, DWORD type, DWORD value)
{
    return DEV_ORIG(d, set_ss_fn, SetSamplerState)(d->self, s, (D3DSAMPLERSTATETYPE)type, value);
}

static DWORD real_get(dev_rec *d, DWORD s, DWORD type)
{
    DWORD value = 0;
    DEV_ORIG(d, get_ss_fn, GetSamplerState)(d->self, s, (D3DSAMPLERSTATETYPE)type, &value);
    return value;
}

static uint32_t levels_of(IDirect3DBaseTexture9 *tex)
{
    return tex ? (uint32_t)IDirect3DBaseTexture9_GetLevelCount(tex) : 0;
}

/* Send the device what stage `s` needs; the first failure is returned. */
static HRESULT reconcile(dev_rec *d, DWORD s)
{
    uint32_t min, maxaniso;
    HRESULT hr = D3D_OK, r;
    int writes = gfx_reconcile(&d->st[s], ANISOTROPY, &min, &maxaniso);
    if (writes & GFX_WRITE_MAXANISO) {
        r = real_set(d, s, D3DSAMP_MAXANISOTROPY, maxaniso);
        if (FAILED(r))
            hr = r;
        d->writes++;
    }
    if (writes & GFX_WRITE_MIN) {
        r = real_set(d, s, D3DSAMP_MINFILTER, min);
        if (FAILED(r) && SUCCEEDED(hr))
            hr = r;
        d->writes++;
    }
    if (writes && !d->forced_once && gfx_forces(&d->st[s])) {
        d->forced_once = 1;
        log_line("device %p: first forced sampler (stage %lu, %lu levels, %ux)", d->self, s,
                 (unsigned long)d->st[s].levels, ANISOTROPY);
    }
    return hr;
}

static void reset_stages(dev_rec *d)
{
    for (DWORD s = 0; s < GFX_STAGES; s++)
        gfx_stage_reset(&d->st[s]);
    d->recording = 0;
}

/* Take the game's values back from the device after a state block changed it. */
static void resync(dev_rec *d)
{
    for (DWORD s = 0; s < GFX_STAGES; s++) {
        IDirect3DBaseTexture9 *tex = NULL;
        uint32_t levels = 0;
        if (SUCCEEDED(IDirect3DDevice9_GetTexture(d->self, s, &tex)) && tex) {
            levels = levels_of(tex);
            IDirect3DBaseTexture9_Release(tex);
        }
        gfx_resync(&d->st[s], real_get(d, s, D3DSAMP_MINFILTER), real_get(d, s, D3DSAMP_MIPFILTER),
                   real_get(d, s, D3DSAMP_MAXANISOTROPY), levels);
        reconcile(d, s);
    }
}

static HRESULT WINAPI dev_SetSamplerState(IDirect3DDevice9 *dev, DWORD s, D3DSAMPLERSTATETYPE type,
                                          DWORD value)
{
    dev_rec *d = REC(dev_rec, dev);
    HRESULT hr;
    if (s >= GFX_STAGES || d->recording)
        return real_set(d, s, type, value);
    switch (type) {
    case D3DSAMP_MIPFILTER:
        hr = real_set(d, s, type, value);
        if (FAILED(hr))
            return hr;
        gfx_set(&d->st[s], type, value);
        return reconcile(d, s);
    case D3DSAMP_MINFILTER:
    case D3DSAMP_MAXANISOTROPY:
        gfx_set(&d->st[s], type, value);
        if (d->st[s].game_aniso && !d->game_aniso_logged) {
            d->game_aniso_logged = 1;
            log_line("device %p: the game set D3DTEXF_ANISOTROPIC itself (stage %lu)", d->self, s);
        }
        return reconcile(d, s);
    default:
        return real_set(d, s, type, value);
    }
}

static HRESULT WINAPI dev_GetSamplerState(IDirect3DDevice9 *dev, DWORD s, D3DSAMPLERSTATETYPE type,
                                          DWORD *value)
{
    dev_rec *d = REC(dev_rec, dev);
    if (s < GFX_STAGES && value && !d->recording) {
        if (type == D3DSAMP_MINFILTER) {
            *value = d->st[s].min;
            return D3D_OK;
        }
        if (type == D3DSAMP_MAXANISOTROPY) {
            *value = d->st[s].maxaniso;
            return D3D_OK;
        }
    }
    return DEV_ORIG(d, get_ss_fn, GetSamplerState)(dev, s, type, value);
}

static HRESULT WINAPI dev_SetTexture(IDirect3DDevice9 *dev, DWORD s, IDirect3DBaseTexture9 *tex)
{
    dev_rec *d = REC(dev_rec, dev);
    HRESULT hr = DEV_ORIG(d, set_tex_fn, SetTexture)(dev, s, tex);
    if (SUCCEEDED(hr) && s < GFX_STAGES && !d->recording) {
        uint32_t levels = levels_of(tex);
        if ((levels > 1) != (d->st[s].levels > 1)) {
            d->st[s].levels = levels;
            reconcile(d, s);
        } else {
            d->st[s].levels = levels;
        }
    }
    return hr;
}

static HRESULT WINAPI dev_Reset(IDirect3DDevice9 *dev, D3DPRESENT_PARAMETERS *pp)
{
    dev_rec *d = REC(dev_rec, dev);
    HRESULT hr = DEV_ORIG(d, reset_fn, Reset)(dev, pp);
    if (SUCCEEDED(hr))
        reset_stages(d);
    return hr;
}

static HRESULT WINAPI dev_ResetEx(IDirect3DDevice9Ex *dev, D3DPRESENT_PARAMETERS *pp,
                                  D3DDISPLAYMODEEX *mode)
{
    dev_rec *d = REC(dev_rec, dev);
    HRESULT hr = ((reset_ex_fn)d->orig[SLOT(IDirect3DDevice9ExVtbl, ResetEx)])(dev, pp, mode);
    if (SUCCEEDED(hr))
        reset_stages(d);
    return hr;
}

/* ---------------------------------------------------- state block hooks */

static HRESULT WINAPI block_Apply(IDirect3DStateBlock9 *sb)
{
    block_rec *b = REC(block_rec, sb);
    HRESULT hr = ((apply_fn)b->orig[SLOT(IDirect3DStateBlock9Vtbl, Apply)])(sb);
    if (SUCCEEDED(hr)) {
        b->dev->applies++;
        resync(b->dev);
    }
    return hr;
}

static ULONG WINAPI block_Release(IDirect3DStateBlock9 *sb)
{
    block_rec *b = REC(block_rec, sb);
    ULONG n = ((release_fn)b->orig[SLOT(IDirect3DStateBlock9Vtbl, Release)])((IUnknown *)sb);
    if (!n)
        free_rec(b);
    return n;
}

static void hook_block(dev_rec *d, IDirect3DStateBlock9 *sb)
{
    block_rec *b;
    if (!sb || !(b = alloc_rec(sizeof(*b))))
        return;
    ADOPT(b, sb, SLOTS(IDirect3DStateBlock9Vtbl));
    b->dev = d;
    b->vtbl[SLOT(IDirect3DStateBlock9Vtbl, Apply)] = (void *)block_Apply;
    b->vtbl[SLOT(IDirect3DStateBlock9Vtbl, Release)] = (void *)block_Release;
    d->blocks++;
}

static HRESULT WINAPI dev_CreateStateBlock(IDirect3DDevice9 *dev, D3DSTATEBLOCKTYPE type,
                                           IDirect3DStateBlock9 **sb)
{
    dev_rec *d = REC(dev_rec, dev);
    HRESULT hr = DEV_ORIG(d, create_sb_fn, CreateStateBlock)(dev, type, sb);
    if (SUCCEEDED(hr) && sb)
        hook_block(d, *sb);
    return hr;
}

static HRESULT WINAPI dev_BeginStateBlock(IDirect3DDevice9 *dev)
{
    dev_rec *d = REC(dev_rec, dev);
    HRESULT hr = DEV_ORIG(d, begin_sb_fn, BeginStateBlock)(dev);
    if (SUCCEEDED(hr))
        d->recording = 1;
    return hr;
}

static HRESULT WINAPI dev_EndStateBlock(IDirect3DDevice9 *dev, IDirect3DStateBlock9 **sb)
{
    dev_rec *d = REC(dev_rec, dev);
    HRESULT hr = DEV_ORIG(d, end_sb_fn, EndStateBlock)(dev, sb);
    d->recording = 0;
    if (SUCCEEDED(hr) && sb)
        hook_block(d, *sb);
    return hr;
}

static ULONG WINAPI dev_Release(IDirect3DDevice9 *dev)
{
    dev_rec *d = REC(dev_rec, dev);
    ULONG n = DEV_ORIG(d, release_fn, Release)((IUnknown *)dev);
    if (!n) {
        log_line("device %p released: %lu sampler writes, %lu state blocks, %lu applies", dev,
                 d->writes, d->blocks, d->applies);
        free_rec(d);
    }
    return n;
}

static void hook_device(IDirect3DDevice9 *dev)
{
    dev_rec *d;
    int ex;
    if (!dev || !(d = alloc_rec(sizeof(*d))))
        return;
    ex = is_same_object((IUnknown *)dev, &IID_IDirect3DDevice9Ex);
    ADOPT(d, dev, ex ? SLOTS(IDirect3DDevice9ExVtbl) : SLOTS(IDirect3DDevice9Vtbl));
    d->self = dev;
    reset_stages(d);
    d->vtbl[DEV_SLOT(Release)] = (void *)dev_Release;
    d->vtbl[DEV_SLOT(Reset)] = (void *)dev_Reset;
    d->vtbl[DEV_SLOT(SetSamplerState)] = (void *)dev_SetSamplerState;
    d->vtbl[DEV_SLOT(GetSamplerState)] = (void *)dev_GetSamplerState;
    d->vtbl[DEV_SLOT(SetTexture)] = (void *)dev_SetTexture;
    d->vtbl[DEV_SLOT(CreateStateBlock)] = (void *)dev_CreateStateBlock;
    d->vtbl[DEV_SLOT(BeginStateBlock)] = (void *)dev_BeginStateBlock;
    d->vtbl[DEV_SLOT(EndStateBlock)] = (void *)dev_EndStateBlock;
    if (ex)
        d->vtbl[SLOT(IDirect3DDevice9ExVtbl, ResetEx)] = (void *)dev_ResetEx;
    log_line("device %p hooked%s: anisotropy %ux", dev, ex ? " (Ex)" : "", ANISOTROPY);
}

/* --------------------------------------------------------- IDirect3D9 */

typedef HRESULT(WINAPI *create_dev_fn)(IDirect3D9 *, UINT, D3DDEVTYPE, HWND, DWORD,
                                       D3DPRESENT_PARAMETERS *, IDirect3DDevice9 **);
typedef HRESULT(WINAPI *create_dev_ex_fn)(IDirect3D9Ex *, UINT, D3DDEVTYPE, HWND, DWORD,
                                          D3DPRESENT_PARAMETERS *, D3DDISPLAYMODEEX *,
                                          IDirect3DDevice9Ex **);

static HRESULT WINAPI d3d_CreateDevice(IDirect3D9 *d3d, UINT adapter, D3DDEVTYPE type, HWND hwnd,
                                       DWORD flags, D3DPRESENT_PARAMETERS *pp,
                                       IDirect3DDevice9 **out)
{
    d3d_rec *r = REC(d3d_rec, d3d);
    HRESULT hr = ((create_dev_fn)r->orig[SLOT(IDirect3D9Vtbl, CreateDevice)])(d3d, adapter, type, hwnd,
                                                                             flags, pp, out);
    if (SUCCEEDED(hr) && out)
        hook_device(*out);
    return hr;
}

static HRESULT WINAPI d3d_CreateDeviceEx(IDirect3D9Ex *d3d, UINT adapter, D3DDEVTYPE type, HWND hwnd,
                                         DWORD flags, D3DPRESENT_PARAMETERS *pp,
                                         D3DDISPLAYMODEEX *mode, IDirect3DDevice9Ex **out)
{
    d3d_rec *r = REC(d3d_rec, d3d);
    HRESULT hr = ((create_dev_ex_fn)r->orig[SLOT(IDirect3D9ExVtbl, CreateDeviceEx)])(
        d3d, adapter, type, hwnd, flags, pp, mode, out);
    if (SUCCEEDED(hr) && out)
        hook_device((IDirect3DDevice9 *)*out);
    return hr;
}

static ULONG WINAPI d3d_Release(IDirect3D9 *d3d)
{
    d3d_rec *r = REC(d3d_rec, d3d);
    ULONG n = ((release_fn)r->orig[SLOT(IDirect3D9Vtbl, Release)])((IUnknown *)d3d);
    if (!n)
        free_rec(r);
    return n;
}

static void hook_d3d(IDirect3D9 *d3d)
{
    d3d_rec *r;
    int ex;
    if (!d3d || !(r = alloc_rec(sizeof(*r))))
        return;
    ex = is_same_object((IUnknown *)d3d, &IID_IDirect3D9Ex);
    ADOPT(r, d3d, ex ? SLOTS(IDirect3D9ExVtbl) : SLOTS(IDirect3D9Vtbl));
    r->vtbl[SLOT(IDirect3D9Vtbl, Release)] = (void *)d3d_Release;
    r->vtbl[SLOT(IDirect3D9Vtbl, CreateDevice)] = (void *)d3d_CreateDevice;
    if (ex)
        r->vtbl[SLOT(IDirect3D9ExVtbl, CreateDeviceEx)] = (void *)d3d_CreateDeviceEx;
}

/* ------------------------------------------------------------ exports */

IDirect3D9 *WINAPI proxy_Direct3DCreate9(UINT sdk)
{
    typedef IDirect3D9 *(WINAPI * fn)(UINT);
    fn f = (fn)real_proc("Direct3DCreate9");
    IDirect3D9 *d3d = f ? f(sdk) : NULL;
    hook_d3d(d3d);
    return d3d;
}

HRESULT WINAPI proxy_Direct3DCreate9Ex(UINT sdk, IDirect3D9Ex **out)
{
    typedef HRESULT(WINAPI * fn)(UINT, IDirect3D9Ex **);
    fn f = (fn)real_proc("Direct3DCreate9Ex");
    HRESULT hr = f ? f(sdk, out) : D3DERR_NOTAVAILABLE;
    if (SUCCEEDED(hr) && out)
        hook_d3d((IDirect3D9 *)*out);
    return hr;
}

void *WINAPI proxy_Direct3DShaderValidatorCreate9(void)
{
    typedef void *(WINAPI * fn)(void);
    fn f = (fn)real_proc("Direct3DShaderValidatorCreate9");
    return f ? f() : NULL;
}

int WINAPI proxy_D3DPERF_BeginEvent(D3DCOLOR color, const WCHAR *name)
{
    typedef int(WINAPI * fn)(D3DCOLOR, const WCHAR *);
    fn f = (fn)real_proc("D3DPERF_BeginEvent");
    return f ? f(color, name) : 0;
}

int WINAPI proxy_D3DPERF_EndEvent(void)
{
    typedef int(WINAPI * fn)(void);
    fn f = (fn)real_proc("D3DPERF_EndEvent");
    return f ? f() : 0;
}

DWORD WINAPI proxy_D3DPERF_GetStatus(void)
{
    typedef DWORD(WINAPI * fn)(void);
    fn f = (fn)real_proc("D3DPERF_GetStatus");
    return f ? f() : 0;
}

BOOL WINAPI proxy_D3DPERF_QueryRepeatFrame(void)
{
    typedef BOOL(WINAPI * fn)(void);
    fn f = (fn)real_proc("D3DPERF_QueryRepeatFrame");
    return f ? f() : FALSE;
}

void WINAPI proxy_D3DPERF_SetMarker(D3DCOLOR color, const WCHAR *name)
{
    typedef void(WINAPI * fn)(D3DCOLOR, const WCHAR *);
    fn f = (fn)real_proc("D3DPERF_SetMarker");
    if (f)
        f(color, name);
}

void WINAPI proxy_D3DPERF_SetOptions(DWORD options)
{
    typedef void(WINAPI * fn)(DWORD);
    fn f = (fn)real_proc("D3DPERF_SetOptions");
    if (f)
        f(options);
}

void WINAPI proxy_D3DPERF_SetRegion(D3DCOLOR color, const WCHAR *name)
{
    typedef void(WINAPI * fn)(D3DCOLOR, const WCHAR *);
    fn f = (fn)real_proc("D3DPERF_SetRegion");
    if (f)
        f(color, name);
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        self_module = inst;
        DisableThreadLibraryCalls(inst);
        InitializeCriticalSection(&init_lock);
        open_log();
        log_line("%s", marker);
    }
    return TRUE;
}
