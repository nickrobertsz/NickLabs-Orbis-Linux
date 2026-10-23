/*
 * orbis-gpu-probe — NickLabs PS4 Orbis Linux
 *
 * Answers, with measurements rather than names:
 *   --gl       Which OpenGL ES renderer actually runs, and does a draw land
 *              the expected pixels? (EGL surfaceless; no window, no modeset)
 *   --vk       Which Vulkan physical devices exist (type, driver, heaps,
 *              budgets, queue families)?
 *   --compute  Is Vulkan compute *correct* on the chosen device and queue?
 *              fill-buffer boundaries, copy round-trips, large dispatch
 *              geometry, exact integer SAXPY, shared-memory reduction with
 *              atomics, and repeated submissions with fence timeouts.
 *
 * Every GPU wait is bounded (--timeout-ms). A timeout or VK_ERROR_DEVICE_LOST
 * stops the suite and is reported as such; the caller (orbisd / the
 * inference harness) runs this in a child process with its own deadline.
 *
 * No link-time dependency on libEGL/libGLESv2/libvulkan: they are dlopen()ed,
 * so the probe also reports *missing* libraries instead of failing to start.
 *
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>

#include "shaders_spv.h"

#define PROBE_VERSION "0.1.0"

/* ------------------------------------------------------------------ JSON */
static FILE *J;
static int j_need_comma[64];
static int j_depth;

static void j_raw(const char *s) { fputs(s, J); }
static void j_comma(void) {
    if (j_need_comma[j_depth]) fputc(',', J);
    j_need_comma[j_depth] = 1;
}
static void j_str_val(const char *s) {
    fputc('"', J);
    for (const unsigned char *p = (const unsigned char *)(s ? s : ""); *p; p++) {
        if (*p == '"' || *p == '\\') { fputc('\\', J); fputc(*p, J); }
        else if (*p < 0x20) fprintf(J, "\\u%04x", *p);
        else fputc(*p, J);
    }
    fputc('"', J);
}
static void j_key(const char *k) { j_comma(); j_str_val(k); fputc(':', J); }
static void j_open(const char *k, char c) {
    if (k) j_key(k); else j_comma();
    fputc(c, J);
    j_need_comma[++j_depth] = 0;
}
static void j_close(char c) { fputc(c, J); j_depth--; }
static void j_kstr(const char *k, const char *v) { j_key(k); if (v) j_str_val(v); else j_raw("null"); }
static void j_kint(const char *k, long long v) { j_key(k); fprintf(J, "%lld", v); }
static void j_kuint(const char *k, unsigned long long v) { j_key(k); fprintf(J, "%llu", v); }
static void j_kdbl(const char *k, double v) { j_key(k); fprintf(J, "%.6f", v); }
static void j_kbool(const char *k, bool v) { j_key(k); j_raw(v ? "true" : "false"); }

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* ------------------------------------------------------------------ GL */
typedef void *EGLDisplay, *EGLConfig, *EGLContext, *EGLSurface;
typedef int32_t EGLint;
typedef unsigned int EGLBoolean, EGLenum;
#define EGL_NONE 0x3038
#define EGL_RENDERABLE_TYPE 0x3040
#define EGL_OPENGL_ES2_BIT 0x0004
#define EGL_OPENGL_ES_API 0x30A0
#define EGL_CONTEXT_CLIENT_VERSION 0x3098
#define EGL_VENDOR 0x3053
#define EGL_VERSION 0x3054
#define EGL_EXTENSIONS 0x3055
#define EGL_PLATFORM_SURFACELESS_MESA 0x31DD
#define EGL_SURFACE_TYPE 0x3033
#define GL_VENDOR 0x1F00
#define GL_RENDERER 0x1F01
#define GL_VERSION 0x1F02
#define GL_SHADING_LANGUAGE_VERSION 0x8B8C
#define GL_FRAMEBUFFER 0x8D40
#define GL_RENDERBUFFER 0x8D41
#define GL_RGBA8 0x8058
#define GL_COLOR_ATTACHMENT0 0x8CE0
#define GL_FRAMEBUFFER_COMPLETE 0x8CD5
#define GL_COLOR_BUFFER_BIT 0x4000
#define GL_RGBA 0x1908
#define GL_UNSIGNED_BYTE 0x1401
#define GL_TRIANGLES 0x0004
#define GL_VERTEX_SHADER 0x8B31
#define GL_FRAGMENT_SHADER 0x8B30
#define GL_COMPILE_STATUS 0x8B81
#define GL_LINK_STATUS 0x8B82
#define GL_FLOAT 0x1406

static int probe_gl(void) {
    void *egl = dlopen("libEGL.so.1", RTLD_NOW | RTLD_LOCAL);
    j_open("gl", '{');
    if (!egl) { j_kstr("error", "libEGL.so.1 not found"); j_close('}'); return 1; }
#define EGLF(ret, name, ...) ret (*name)(__VA_ARGS__) = (ret (*)(__VA_ARGS__))dlsym(egl, #name)
    EGLF(void *, eglGetProcAddress, const char *);
    EGLF(EGLDisplay, eglGetDisplay, void *);
    EGLF(EGLBoolean, eglInitialize, EGLDisplay, EGLint *, EGLint *);
    EGLF(EGLBoolean, eglBindAPI, EGLenum);
    EGLF(EGLBoolean, eglChooseConfig, EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *);
    EGLF(EGLContext, eglCreateContext, EGLDisplay, EGLConfig, EGLContext, const EGLint *);
    EGLF(EGLBoolean, eglMakeCurrent, EGLDisplay, EGLSurface, EGLSurface, EGLContext);
    EGLF(const char *, eglQueryString, EGLDisplay, EGLint);
    EGLF(EGLBoolean, eglTerminate, EGLDisplay);
    EGLF(EGLint, eglGetError, void);
    if (!eglGetProcAddress || !eglInitialize || !eglCreateContext) {
        j_kstr("error", "libEGL lacks core entry points"); j_close('}'); return 1;
    }
    EGLDisplay (*getPlat)(EGLenum, void *, const intptr_t *) =
        (EGLDisplay (*)(EGLenum, void *, const intptr_t *))eglGetProcAddress("eglGetPlatformDisplay");
    EGLDisplay dpy = NULL;
    const char *how = "surfaceless";
    if (getPlat) dpy = getPlat(EGL_PLATFORM_SURFACELESS_MESA, NULL, NULL);
    if (!dpy && eglGetDisplay) { dpy = eglGetDisplay(NULL); how = "default"; }
    EGLint maj = 0, min = 0;
    if (!dpy || !eglInitialize(dpy, &maj, &min)) {
        j_kstr("error", "eglInitialize failed");
        j_kint("egl_error", eglGetError ? eglGetError() : 0);
        j_close('}'); return 1;
    }
    j_kstr("egl_platform", how);
    j_kstr("egl_vendor", eglQueryString(dpy, EGL_VENDOR));
    j_kstr("egl_version", eglQueryString(dpy, EGL_VERSION));
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint cattr[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_SURFACE_TYPE, 0, EGL_NONE};
    EGLConfig cfg = NULL; EGLint n = 0;
    if (!eglChooseConfig(dpy, cattr, &cfg, 1, &n) || n < 1) {
        /* surfaceless contexts may be created without a config (EGL_KHR_no_config_context) */
        cfg = NULL;
    }
    EGLint ctxattr[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    EGLContext ctx = eglCreateContext(dpy, cfg, NULL, ctxattr);
    if (!ctx || !eglMakeCurrent(dpy, NULL, NULL, ctx)) {
        j_kstr("error", "could not create/bind a surfaceless GLES2 context");
        j_kint("egl_error", eglGetError ? eglGetError() : 0);
        eglTerminate(dpy); j_close('}'); return 1;
    }
#define GLF(ret, name, ...) ret (*name)(__VA_ARGS__) = (ret (*)(__VA_ARGS__))eglGetProcAddress(#name)
    GLF(const unsigned char *, glGetString, unsigned);
    GLF(void, glGenFramebuffers, int, unsigned *);
    GLF(void, glBindFramebuffer, unsigned, unsigned);
    GLF(void, glGenRenderbuffers, int, unsigned *);
    GLF(void, glBindRenderbuffer, unsigned, unsigned);
    GLF(void, glRenderbufferStorage, unsigned, unsigned, int, int);
    GLF(void, glFramebufferRenderbuffer, unsigned, unsigned, unsigned, unsigned);
    GLF(unsigned, glCheckFramebufferStatus, unsigned);
    GLF(void, glViewport, int, int, int, int);
    GLF(void, glClearColor, float, float, float, float);
    GLF(void, glClear, unsigned);
    GLF(void, glReadPixels, int, int, int, int, unsigned, unsigned, void *);
    GLF(unsigned, glCreateShader, unsigned);
    GLF(void, glShaderSource, unsigned, int, const char *const *, const int *);
    GLF(void, glCompileShader, unsigned);
    GLF(void, glGetShaderiv, unsigned, unsigned, int *);
    GLF(unsigned, glCreateProgram, void);
    GLF(void, glAttachShader, unsigned, unsigned);
    GLF(void, glBindAttribLocation, unsigned, unsigned, const char *);
    GLF(void, glLinkProgram, unsigned);
    GLF(void, glGetProgramiv, unsigned, unsigned, int *);
    GLF(void, glUseProgram, unsigned);
    GLF(void, glVertexAttribPointer, unsigned, int, unsigned, unsigned char, int, const void *);
    GLF(void, glEnableVertexAttribArray, unsigned);
    GLF(void, glDrawArrays, unsigned, int, int);
    GLF(void, glFinish, void);
    if (!glGetString) { j_kstr("error", "glGetString unavailable"); j_close('}'); return 1; }
    j_kstr("vendor", (const char *)glGetString(GL_VENDOR));
    j_kstr("renderer", (const char *)glGetString(GL_RENDERER));
    j_kstr("version", (const char *)glGetString(GL_VERSION));
    j_kstr("glsl", (const char *)glGetString(GL_SHADING_LANGUAGE_VERSION));

    /* Draw test: clear to blue, draw a triangle covering the left half red,
     * read back and check both colours. Proves the renderer executes work,
     * not just that it reports a name. */
    bool draw_ok = false;
    const char *draw_err = NULL;
    if (glGenFramebuffers && glReadPixels && glDrawArrays && glCreateShader) {
        enum { W = 64, H = 64 };
        unsigned fbo, rb;
        glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, W, H);
        glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
            draw_err = "framebuffer incomplete";
        } else {
            static const char *vs = "attribute vec2 p; void main(){ gl_Position = vec4(p,0.0,1.0); }";
            static const char *fs = "precision mediump float; void main(){ gl_FragColor = vec4(1.0,0.0,0.0,1.0); }";
            unsigned v = glCreateShader(GL_VERTEX_SHADER), f = glCreateShader(GL_FRAGMENT_SHADER);
            glShaderSource(v, 1, &vs, NULL); glCompileShader(v);
            glShaderSource(f, 1, &fs, NULL); glCompileShader(f);
            int okv = 0, okf = 0, okp = 0;
            glGetShaderiv(v, GL_COMPILE_STATUS, &okv); glGetShaderiv(f, GL_COMPILE_STATUS, &okf);
            unsigned prog = glCreateProgram();
            glAttachShader(prog, v); glAttachShader(prog, f);
            glBindAttribLocation(prog, 0, "p"); glLinkProgram(prog);
            glGetProgramiv(prog, GL_LINK_STATUS, &okp);
            if (!okv || !okf || !okp) {
                draw_err = "shader compile/link failed";
            } else {
                /* left half: x in [-1,0] */
                static const float tri[] = {-1.f, -1.f, 0.f, -1.f, -1.f, 3.f,  0.f, -1.f, 0.f, 3.f, -1.f, 3.f};
                glViewport(0, 0, W, H);
                glClearColor(0.f, 0.f, 1.f, 1.f); glClear(GL_COLOR_BUFFER_BIT);
                glUseProgram(prog);
                glVertexAttribPointer(0, 2, GL_FLOAT, 0, 0, tri); glEnableVertexAttribArray(0);
                glDrawArrays(GL_TRIANGLES, 0, 6); glFinish();
                static unsigned char px[W * H * 4];
                glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, px);
                int red = 0, blue = 0, other = 0;
                for (int y = 0; y < H; y++)
                    for (int x = 0; x < W; x++) {
                        unsigned char *q = &px[(y * W + x) * 4];
                        bool left = x < W / 2;
                        if (left && q[0] > 240 && q[1] < 16 && q[2] < 16) red++;
                        else if (!left && q[0] < 16 && q[1] < 16 && q[2] > 240) blue++;
                        else other++;
                    }
                j_kint("draw_red_px", red); j_kint("draw_blue_px", blue); j_kint("draw_wrong_px", other);
                draw_ok = (other == 0 && red == W * H / 2 && blue == W * H / 2);
                if (!draw_ok) draw_err = "pixel mismatch";
            }
        }
    } else {
        draw_err = "GL entry points missing";
    }
    j_kbool("draw_ok", draw_ok);
    if (draw_err) j_kstr("draw_error", draw_err);
    eglMakeCurrent(dpy, NULL, NULL, NULL);
    eglTerminate(dpy);
    j_close('}');
    return draw_ok ? 0 : 1;
}

/* ------------------------------------------------------------------ Vulkan */
static void *vklib;
static PFN_vkGetInstanceProcAddr gipa;
static VkInstance inst;
#define IF(name) PFN_##name name = (PFN_##name)gipa(inst, #name)

static const char *vkres(VkResult r) {
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    default: return "VK_ERROR_OTHER";
    }
}

static int vk_init(void) {
    vklib = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!vklib) return -1;
    gipa = (PFN_vkGetInstanceProcAddr)dlsym(vklib, "vkGetInstanceProcAddr");
    if (!gipa) return -2;
    PFN_vkEnumerateInstanceVersion eiv = (PFN_vkEnumerateInstanceVersion)gipa(NULL, "vkEnumerateInstanceVersion");
    uint32_t api = VK_API_VERSION_1_0;
    if (eiv) eiv(&api);
    PFN_vkCreateInstance ci = (PFN_vkCreateInstance)gipa(NULL, "vkCreateInstance");
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "orbis-gpu-probe",
                            .apiVersion = api >= VK_API_VERSION_1_2 ? VK_API_VERSION_1_2 : VK_API_VERSION_1_1};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
    VkResult r = ci(&ici, NULL, &inst);
    if (r != VK_SUCCESS) return (int)r;
    return 0;
}

static bool has_dev_ext(VkPhysicalDevice pd, const char *name) {
    IF(vkEnumerateDeviceExtensionProperties);
    uint32_t n = 0;
    vkEnumerateDeviceExtensionProperties(pd, NULL, &n, NULL);
    VkExtensionProperties *e = calloc(n ? n : 1, sizeof(*e));
    vkEnumerateDeviceExtensionProperties(pd, NULL, &n, e);
    bool found = false;
    for (uint32_t i = 0; i < n; i++)
        if (!strcmp(e[i].extensionName, name)) { found = true; break; }
    free(e);
    return found;
}

static void describe_device(VkPhysicalDevice pd, uint32_t idx) {
    IF(vkGetPhysicalDeviceProperties2);
    IF(vkGetPhysicalDeviceMemoryProperties2);
    IF(vkGetPhysicalDeviceQueueFamilyProperties);
    VkPhysicalDeviceDriverProperties drv = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
    VkPhysicalDeviceProperties2 p2 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &drv};
    vkGetPhysicalDeviceProperties2(pd, &p2);
    VkPhysicalDeviceProperties *p = &p2.properties;
    j_open(NULL, '{');
    j_kint("index", idx);
    j_kstr("name", p->deviceName);
    j_kint("device_type", p->deviceType);
    j_kint("vendor_id", p->vendorID);
    j_kint("device_id", p->deviceID);
    char buf[64];
    snprintf(buf, sizeof buf, "%u.%u.%u", VK_API_VERSION_MAJOR(p->apiVersion),
             VK_API_VERSION_MINOR(p->apiVersion), VK_API_VERSION_PATCH(p->apiVersion));
    j_kstr("api_version", buf);
    j_kint("driver_id", drv.driverID);
    j_kstr("driver_name", drv.driverName);
    j_kstr("driver_info", drv.driverInfo);
    j_kuint("max_compute_workgroup_count_x", p->limits.maxComputeWorkGroupCount[0]);
    j_kuint("max_storage_buffer_range", p->limits.maxStorageBufferRange);
    bool budget = has_dev_ext(pd, VK_EXT_MEMORY_BUDGET_EXTENSION_NAME);
    VkPhysicalDeviceMemoryBudgetPropertiesEXT mb = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT};
    VkPhysicalDeviceMemoryProperties2 m2 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2,
                                            .pNext = budget ? &mb : NULL};
    vkGetPhysicalDeviceMemoryProperties2(pd, &m2);
    j_kbool("memory_budget_ext", budget);
    j_open("heaps", '[');
    for (uint32_t h = 0; h < m2.memoryProperties.memoryHeapCount; h++) {
        j_open(NULL, '{');
        j_kuint("size", m2.memoryProperties.memoryHeaps[h].size);
        j_kbool("device_local", m2.memoryProperties.memoryHeaps[h].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT);
        if (budget) { j_kuint("budget", mb.heapBudget[h]); j_kuint("usage", mb.heapUsage[h]); }
        j_close('}');
    }
    j_close(']');
    uint32_t qn = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, NULL);
    VkQueueFamilyProperties *q = calloc(qn ? qn : 1, sizeof(*q));
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, q);
    j_open("queue_families", '[');
    for (uint32_t i = 0; i < qn; i++) {
        j_open(NULL, '{');
        j_kint("index", i);
        j_kint("count", q[i].queueCount);
        j_kbool("graphics", q[i].queueFlags & VK_QUEUE_GRAPHICS_BIT);
        j_kbool("compute", q[i].queueFlags & VK_QUEUE_COMPUTE_BIT);
        j_kbool("transfer", q[i].queueFlags & VK_QUEUE_TRANSFER_BIT);
        j_close('}');
    }
    j_close(']');
    free(q);
    j_close('}');
}

/* ----- compute suite */
struct ctx {
    VkPhysicalDevice pd;
    VkDevice dev;
    VkQueue queue;
    uint32_t qfam;
    VkCommandPool pool;
    VkPhysicalDeviceMemoryProperties mem;
    uint64_t timeout_ns;
    bool fatal;          /* device lost / timeout: stop everything */
    const char *fatal_why;
    /* device fns */
    PFN_vkCreateBuffer CreateBuffer; PFN_vkDestroyBuffer DestroyBuffer;
    PFN_vkGetBufferMemoryRequirements GetBufMemReq; PFN_vkAllocateMemory AllocateMemory;
    PFN_vkFreeMemory FreeMemory; PFN_vkBindBufferMemory BindBufferMemory;
    PFN_vkMapMemory MapMemory; PFN_vkUnmapMemory UnmapMemory;
    PFN_vkAllocateCommandBuffers AllocCB; PFN_vkFreeCommandBuffers FreeCB;
    PFN_vkBeginCommandBuffer BeginCB; PFN_vkEndCommandBuffer EndCB;
    PFN_vkCmdFillBuffer CmdFill; PFN_vkCmdCopyBuffer CmdCopy;
    PFN_vkCmdPipelineBarrier CmdBarrier; PFN_vkCmdBindPipeline CmdBindPipe;
    PFN_vkCmdBindDescriptorSets CmdBindDS; PFN_vkCmdPushConstants CmdPush;
    PFN_vkCmdDispatch CmdDispatch; PFN_vkQueueSubmit QueueSubmit;
    PFN_vkCreateFence CreateFence; PFN_vkDestroyFence DestroyFence;
    PFN_vkWaitForFences WaitFences; PFN_vkCreateShaderModule CreateSM;
    PFN_vkDestroyShaderModule DestroySM; PFN_vkCreateDescriptorSetLayout CreateDSL;
    PFN_vkDestroyDescriptorSetLayout DestroyDSL; PFN_vkCreatePipelineLayout CreatePL;
    PFN_vkDestroyPipelineLayout DestroyPL; PFN_vkCreateComputePipelines CreateCP;
    PFN_vkDestroyPipeline DestroyPipe; PFN_vkCreateDescriptorPool CreateDP;
    PFN_vkDestroyDescriptorPool DestroyDP; PFN_vkAllocateDescriptorSets AllocDS;
    PFN_vkUpdateDescriptorSets UpdateDS; PFN_vkDeviceWaitIdle DeviceWaitIdle;
};

struct buf { VkBuffer b; VkDeviceMemory m; VkDeviceSize size; void *map; };

static int find_mem(struct ctx *c, uint32_t bits, VkMemoryPropertyFlags want, VkMemoryPropertyFlags avoid) {
    for (uint32_t i = 0; i < c->mem.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (c->mem.memoryTypes[i].propertyFlags & want) == want &&
            !(c->mem.memoryTypes[i].propertyFlags & avoid))
            return (int)i;
    for (uint32_t i = 0; i < c->mem.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (c->mem.memoryTypes[i].propertyFlags & want) == want) return (int)i;
    return -1;
}

static VkResult mkbuf(struct ctx *c, struct buf *o, VkDeviceSize size, bool host) {
    memset(o, 0, sizeof *o);
    o->size = size;
    VkBufferCreateInfo bi = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = size,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    VkResult r = c->CreateBuffer(c->dev, &bi, NULL, &o->b);
    if (r) return r;
    VkMemoryRequirements mr;
    c->GetBufMemReq(c->dev, o->b, &mr);
    int t = host ? find_mem(c, mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, 0)
                 : find_mem(c, mr.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
    if (t < 0) return VK_ERROR_FEATURE_NOT_PRESENT;
    VkMemoryAllocateInfo ai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = mr.size, .memoryTypeIndex = (uint32_t)t};
    r = c->AllocateMemory(c->dev, &ai, NULL, &o->m);
    if (r) return r;
    r = c->BindBufferMemory(c->dev, o->b, o->m, 0);
    if (r) return r;
    if (host) r = c->MapMemory(c->dev, o->m, 0, VK_WHOLE_SIZE, 0, &o->map);
    return r;
}

static void rmbuf(struct ctx *c, struct buf *o) {
    if (o->map) c->UnmapMemory(c->dev, o->m);
    if (o->b) c->DestroyBuffer(c->dev, o->b, NULL);
    if (o->m) c->FreeMemory(c->dev, o->m, NULL);
    memset(o, 0, sizeof *o);
}

static VkCommandBuffer cb_begin(struct ctx *c) {
    VkCommandBufferAllocateInfo ai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = c->pool,
                                      .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer cb;
    if (c->AllocCB(c->dev, &ai, &cb)) return VK_NULL_HANDLE;
    VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                   .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    c->BeginCB(cb, &bi);
    return cb;
}

static void barrier_all(struct ctx *c, VkCommandBuffer cb) {
    VkMemoryBarrier mb = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
                          .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_WRITE_BIT,
                          .dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT |
                                           VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_HOST_READ_BIT};
    c->CmdBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                  VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_HOST_BIT,
                  0, 1, &mb, 0, NULL, 0, NULL);
}

/* Submit, wait with timeout. Returns VkResult; marks fatal on TIMEOUT/DEVICE_LOST. */
static VkResult cb_submit(struct ctx *c, VkCommandBuffer cb, double *elapsed) {
    c->EndCB(cb);
    VkFenceCreateInfo fi = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence f;
    VkResult r = c->CreateFence(c->dev, &fi, NULL, &f);
    if (r) return r;
    VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
    double t0 = now_s();
    r = c->QueueSubmit(c->queue, 1, &si, f);
    if (r == VK_SUCCESS) r = c->WaitFences(c->dev, 1, &f, VK_TRUE, c->timeout_ns);
    if (elapsed) *elapsed = now_s() - t0;
    if (r == VK_TIMEOUT || r == VK_ERROR_DEVICE_LOST) {
        c->fatal = true;
        c->fatal_why = vkres(r);
        /* Do not free resources the GPU may still reference. */
        return r;
    }
    c->DestroyFence(c->dev, f, NULL);
    c->FreeCB(c->dev, c->pool, 1, &cb);
    return r;
}

struct pipe { VkShaderModule sm; VkDescriptorSetLayout dsl; VkPipelineLayout pl; VkPipeline p; VkDescriptorPool dp; VkDescriptorSet ds; };

static VkResult mkpipe(struct ctx *c, struct pipe *o, const uint32_t *code, size_t size, int nbuf, uint32_t pc_size) {
    memset(o, 0, sizeof *o);
    VkShaderModuleCreateInfo smi = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = size, .pCode = code};
    VkResult r = c->CreateSM(c->dev, &smi, NULL, &o->sm);
    if (r) return r;
    VkDescriptorSetLayoutBinding b[2];
    for (int i = 0; i < nbuf; i++)
        b[i] = (VkDescriptorSetLayoutBinding){.binding = (uint32_t)i, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                                              .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo dli = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = (uint32_t)nbuf, .pBindings = b};
    if ((r = c->CreateDSL(c->dev, &dli, NULL, &o->dsl))) return r;
    VkPushConstantRange pcr = {.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT, .offset = 0, .size = pc_size};
    VkPipelineLayoutCreateInfo pli = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1,
                                      .pSetLayouts = &o->dsl, .pushConstantRangeCount = 1, .pPushConstantRanges = &pcr};
    if ((r = c->CreatePL(c->dev, &pli, NULL, &o->pl))) return r;
    VkComputePipelineCreateInfo cpi = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_COMPUTE_BIT,
                  .module = o->sm, .pName = "main"},
        .layout = o->pl};
    if ((r = c->CreateCP(c->dev, VK_NULL_HANDLE, 1, &cpi, NULL, &o->p))) return r;
    VkDescriptorPoolSize ps = {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 2};
    VkDescriptorPoolCreateInfo dpi = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &ps};
    if ((r = c->CreateDP(c->dev, &dpi, NULL, &o->dp))) return r;
    VkDescriptorSetAllocateInfo dai = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = o->dp,
                                       .descriptorSetCount = 1, .pSetLayouts = &o->dsl};
    return c->AllocDS(c->dev, &dai, &o->ds);
}

static void bindbufs(struct ctx *c, struct pipe *o, struct buf *a, struct buf *b2) {
    VkDescriptorBufferInfo bi[2] = {{a->b, 0, VK_WHOLE_SIZE}, {b2 ? b2->b : VK_NULL_HANDLE, 0, VK_WHOLE_SIZE}};
    VkWriteDescriptorSet w[2] = {
        {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = o->ds, .dstBinding = 0, .descriptorCount = 1,
         .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bi[0]},
        {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = o->ds, .dstBinding = 1, .descriptorCount = 1,
         .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bi[1]}};
    c->UpdateDS(c->dev, b2 ? 2 : 1, w, 0, NULL);
}

static void rmpipe(struct ctx *c, struct pipe *o) {
    if (o->dp) c->DestroyDP(c->dev, o->dp, NULL);
    if (o->p) c->DestroyPipe(c->dev, o->p, NULL);
    if (o->pl) c->DestroyPL(c->dev, o->pl, NULL);
    if (o->dsl) c->DestroyDSL(c->dev, o->dsl, NULL);
    if (o->sm) c->DestroySM(c->dev, o->sm, NULL);
}

static uint32_t xs32(uint32_t *s) { uint32_t x = *s; x ^= x << 13; x ^= x >> 17; x ^= x << 5; return *s = x; }

static int n_pass, n_fail;
static void result(const char *name, bool ok, double secs, const char *fmt, ...) {
    char msg[256] = "";
    if (fmt) { va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap); }
    j_open(NULL, '{');
    j_kstr("test", name);
    j_kbool("ok", ok);
    j_kdbl("seconds", secs);
    if (*msg) j_kstr("detail", msg);
    j_close('}');
    if (ok) n_pass++; else n_fail++;
    fflush(J);
}

/* T1: vkCmdFillBuffer around the 4 KiB threshold where RADV switches from
 * CP DMA to a compute fill shader (linux-psx 0005 investigated this path). */
static void t_fill(struct ctx *c, VkDeviceSize size) {
    const VkDeviceSize G = 256;
    char name[64]; snprintf(name, sizeof name, "fill_%llu", (unsigned long long)size);
    struct buf d, up, down;
    double t0 = now_s(), el = 0;
    /* Separate upload and readback staging buffers: host writes happen at
     * record time, GPU copies at submit time. */
    if (mkbuf(c, &d, size + 2 * G, false) || mkbuf(c, &up, size + 2 * G, true) || mkbuf(c, &down, size + 2 * G, true)) {
        result(name, false, 0, "allocation failed"); rmbuf(c, &d); rmbuf(c, &up); rmbuf(c, &down); return;
    }
    uint32_t *upp = up.map, *hp = down.map;
    for (VkDeviceSize i = 0; i < (size + 2 * G) / 4; i++) upp[i] = 0xA5A5A5A5u;
    memset(down.map, 0, size + 2 * G);
    VkCommandBuffer cb = cb_begin(c);
    VkBufferCopy cp = {0, 0, size + 2 * G};
    c->CmdCopy(cb, up.b, d.b, 1, &cp);
    barrier_all(c, cb);
    c->CmdFill(cb, d.b, G, size, 0x5A5A5A5Au);
    barrier_all(c, cb);
    c->CmdCopy(cb, d.b, down.b, 1, &cp);
    barrier_all(c, cb);
    VkResult r = cb_submit(c, cb, &el);
    if (r) { result(name, false, el, "%s", vkres(r)); if (!c->fatal) { rmbuf(c, &d); rmbuf(c, &up); rmbuf(c, &down); } return; }
    long bad_guard = 0, bad_fill = 0;
    for (VkDeviceSize i = 0; i < (size + 2 * G) / 4; i++) {
        bool in = i * 4 >= G && i * 4 < G + size;
        if (in && hp[i] != 0x5A5A5A5Au) bad_fill++;
        if (!in && hp[i] != 0xA5A5A5A5u) bad_guard++;
    }
    result(name, !bad_guard && !bad_fill, now_s() - t0, "bad_fill_words=%ld bad_guard_words=%ld gpu_s=%.4f", bad_fill, bad_guard, el);
    rmbuf(c, &d); rmbuf(c, &up); rmbuf(c, &down);
}

/* T2: host -> device-local -> host copy round trip */
static void t_copy(struct ctx *c, VkDeviceSize size) {
    char name[64]; snprintf(name, sizeof name, "copy_%llu", (unsigned long long)size);
    struct buf d, h, h2;
    double t0 = now_s(), el = 0;
    if (mkbuf(c, &d, size, false) || mkbuf(c, &h, size, true) || mkbuf(c, &h2, size, true)) {
        result(name, false, 0, "allocation failed"); rmbuf(c, &d); rmbuf(c, &h); rmbuf(c, &h2); return;
    }
    uint32_t s = 0x1234567u, *a = h.map, *b = h2.map;
    for (VkDeviceSize i = 0; i < size / 4; i++) a[i] = xs32(&s);
    memset(b, 0, size);
    VkCommandBuffer cb = cb_begin(c);
    VkBufferCopy cp = {0, 0, size};
    c->CmdCopy(cb, h.b, d.b, 1, &cp); barrier_all(c, cb);
    c->CmdCopy(cb, d.b, h2.b, 1, &cp); barrier_all(c, cb);
    VkResult r = cb_submit(c, cb, &el);
    if (r) { result(name, false, el, "%s", vkres(r)); return; }
    long bad = 0;
    for (VkDeviceSize i = 0; i < size / 4; i++) bad += a[i] != b[i];
    result(name, !bad, now_s() - t0, "mismatched_words=%ld gpu_s=%.4f", bad, el);
    rmbuf(c, &d); rmbuf(c, &h); rmbuf(c, &h2);
}

/* T3: dispatch geometry — every invocation writes its own linear index */
static void t_index(struct ctx *c, uint32_t gx, uint32_t gy) {
    char name[64]; snprintf(name, sizeof name, "dispatch_%ux%ux64", gx, gy);
    uint64_t count = (uint64_t)gx * gy * 64;
    VkDeviceSize size = count * 4;
    struct buf d, h; struct pipe p;
    double t0 = now_s(), el = 0;
    if (mkbuf(c, &d, size, false) || mkbuf(c, &h, size, true)) { result(name, false, 0, "allocation failed (%llu bytes)", (unsigned long long)size); rmbuf(c, &d); rmbuf(c, &h); return; }
    if (mkpipe(c, &p, spv_index_write, spv_index_write_size, 1, 4)) { result(name, false, 0, "pipeline failed"); return; }
    bindbufs(c, &p, &d, NULL);
    VkCommandBuffer cb = cb_begin(c);
    c->CmdFill(cb, d.b, 0, VK_WHOLE_SIZE, 0xFFFFFFFFu); barrier_all(c, cb);
    c->CmdBindPipe(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.p);
    c->CmdBindDS(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.pl, 0, 1, &p.ds, 0, NULL);
    uint32_t pc = (uint32_t)count;
    c->CmdPush(cb, p.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, 4, &pc);
    c->CmdDispatch(cb, gx, gy, 1); barrier_all(c, cb);
    VkBufferCopy cp = {0, 0, size};
    c->CmdCopy(cb, d.b, h.b, 1, &cp); barrier_all(c, cb);
    VkResult r = cb_submit(c, cb, &el);
    if (r) { result(name, false, el, "%s", vkres(r)); return; }
    uint32_t *o = h.map; long bad = 0; long first_bad = -1;
    for (uint64_t i = 0; i < count; i++) if (o[i] != (uint32_t)i) { if (first_bad < 0) first_bad = (long)i; bad++; }
    result(name, !bad, now_s() - t0, "invocations=%llu wrong=%ld first_wrong=%ld gpu_s=%.4f", (unsigned long long)count, bad, first_bad, el);
    rmpipe(c, &p); rmbuf(c, &d); rmbuf(c, &h);
}

/* T4: exact integer SAXPY, optionally repeated N times in separate submissions */
static void t_saxpy(struct ctx *c, uint32_t n, int repeats) {
    char name[64]; snprintf(name, sizeof name, repeats > 1 ? "saxpy_u32_%u_x%d" : "saxpy_u32_%u", n, repeats);
    VkDeviceSize size = (VkDeviceSize)n * 4;
    struct buf x, y, hx, hy; struct pipe p;
    double t0 = now_s(), el = 0, worst = 0;
    if (mkbuf(c, &x, size, false) || mkbuf(c, &y, size, false) || mkbuf(c, &hx, size, true) || mkbuf(c, &hy, size, true)) {
        result(name, false, 0, "allocation failed"); return;
    }
    if (mkpipe(c, &p, spv_saxpy_u32, spv_saxpy_u32_size, 2, 8)) { result(name, false, 0, "pipeline failed"); return; }
    bindbufs(c, &p, &x, &y);
    uint32_t s = 42, *ax = hx.map, *ay = hy.map;
    for (uint32_t i = 0; i < n; i++) { ax[i] = xs32(&s); ay[i] = xs32(&s); }
    uint32_t *y0 = malloc(size); memcpy(y0, ay, size);
    uint32_t pcs[2] = {n, 2654435761u};
    uint32_t groups = (n + 63) / 64, gx = groups > 65535 ? 65535 : groups, gy = (groups + gx - 1) / gx;
    VkCommandBuffer cb = cb_begin(c);
    VkBufferCopy cp = {0, 0, size};
    c->CmdCopy(cb, hx.b, x.b, 1, &cp); c->CmdCopy(cb, hy.b, y.b, 1, &cp); barrier_all(c, cb);
    VkResult r = cb_submit(c, cb, &el);
    for (int it = 0; it < repeats && !r; it++) {
        cb = cb_begin(c);
        c->CmdBindPipe(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.p);
        c->CmdBindDS(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.pl, 0, 1, &p.ds, 0, NULL);
        c->CmdPush(cb, p.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, 8, pcs);
        c->CmdDispatch(cb, gx, gy, 1); barrier_all(c, cb);
        r = cb_submit(c, cb, &el);
        if (el > worst) worst = el;
        if (r) { result(name, false, el, "%s at iteration %d", vkres(r), it); free(y0); return; }
    }
    cb = cb_begin(c);
    c->CmdCopy(cb, y.b, hy.b, 1, &cp); barrier_all(c, cb);
    if ((r = cb_submit(c, cb, &el))) { result(name, false, el, "%s on readback", vkres(r)); free(y0); return; }
    long bad = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t e = y0[i];
        for (int it = 0; it < repeats; it++) e = pcs[1] * ax[i] + e;
        bad += ay[i] != e;
    }
    result(name, !bad, now_s() - t0, "elements=%u wrong=%ld worst_submit_s=%.4f", n, bad, worst);
    free(y0); rmpipe(c, &p); rmbuf(c, &x); rmbuf(c, &y); rmbuf(c, &hx); rmbuf(c, &hy);
}

/* T5: shared-memory reduction + atomics */
static void t_reduce(struct ctx *c, uint32_t n) {
    char name[64]; snprintf(name, sizeof name, "reduce_%u", n);
    struct buf v, acc, hv, hacc; struct pipe p;
    double t0 = now_s(), el = 0;
    VkDeviceSize size = (VkDeviceSize)n * 4;
    if (mkbuf(c, &v, size, false) || mkbuf(c, &acc, 8, false) || mkbuf(c, &hv, size, true) || mkbuf(c, &hacc, 8, true)) {
        result(name, false, 0, "allocation failed"); return;
    }
    if (mkpipe(c, &p, spv_reduce, spv_reduce_size, 2, 4)) { result(name, false, 0, "pipeline failed"); return; }
    bindbufs(c, &p, &v, &acc);
    uint32_t s = 7, *hp = hv.map, expect = 0;
    for (uint32_t i = 0; i < n; i++) { hp[i] = xs32(&s) & 0xFFFFu; expect += hp[i]; }
    uint32_t groups = (n + 255) / 256, gx = groups > 65535 ? 65535 : groups, gy = (groups + gx - 1) / gx;
    VkCommandBuffer cb = cb_begin(c);
    VkBufferCopy cp = {0, 0, size}, ca = {0, 0, 8};
    c->CmdCopy(cb, hv.b, v.b, 1, &cp);
    c->CmdFill(cb, acc.b, 0, 8, 0); barrier_all(c, cb);
    c->CmdBindPipe(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.p);
    c->CmdBindDS(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.pl, 0, 1, &p.ds, 0, NULL);
    c->CmdPush(cb, p.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, 4, &n);
    c->CmdDispatch(cb, gx, gy, 1); barrier_all(c, cb);
    c->CmdCopy(cb, acc.b, hacc.b, 1, &ca); barrier_all(c, cb);
    VkResult r = cb_submit(c, cb, &el);
    if (r) { result(name, false, el, "%s", vkres(r)); return; }
    uint32_t *o = hacc.map;
    bool ok = o[0] == expect && o[1] == gx * gy;
    result(name, ok, now_s() - t0, "sum=%u expected=%u groups_done=%u expected_groups=%u", o[0], expect, o[1], gx * gy);
    rmpipe(c, &p); rmbuf(c, &v); rmbuf(c, &acc); rmbuf(c, &hv); rmbuf(c, &hacc);
}

static int run_compute(VkPhysicalDevice pd, uint32_t devidx, int want_qfam, uint64_t timeout_ms, int level) {
    struct ctx c = {.pd = pd, .timeout_ns = timeout_ms * 1000000ull};
    IF(vkGetPhysicalDeviceQueueFamilyProperties);
    IF(vkGetPhysicalDeviceMemoryProperties);
    IF(vkCreateDevice);
    IF(vkGetDeviceProcAddr);
    IF(vkGetPhysicalDeviceProperties);
    vkGetPhysicalDeviceMemoryProperties(pd, &c.mem);
    uint32_t qn = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, NULL);
    VkQueueFamilyProperties q[16];
    if (qn > 16) qn = 16;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, q);
    int fam = -1;
    if (want_qfam >= 0 && (uint32_t)want_qfam < qn && (q[want_qfam].queueFlags & VK_QUEUE_COMPUTE_BIT)) fam = want_qfam;
    for (uint32_t i = 0; fam < 0 && i < qn; i++) if (q[i].queueFlags & VK_QUEUE_COMPUTE_BIT) fam = (int)i;
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(pd, &props);
    j_open("compute", '{');
    j_open("device", '{');
    j_kint("index", devidx); j_kstr("name", props.deviceName);
    j_kint("device_type", props.deviceType); j_kint("vendor_id", props.vendorID);
    j_close('}');
    if (fam < 0) { j_kstr("error", "no compute queue family"); j_close('}'); return 1; }
    c.qfam = (uint32_t)fam;
    j_kint("queue_family", fam);
    j_kbool("queue_family_has_graphics", q[fam].queueFlags & VK_QUEUE_GRAPHICS_BIT);
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = c.qfam, .queueCount = 1, .pQueuePriorities = &prio};
    VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qci};
    VkResult r = vkCreateDevice(pd, &dci, NULL, &c.dev);
    if (r) { j_kstr("error", vkres(r)); j_close('}'); return 1; }
#define DF(field, name) c.field = (PFN_##name)vkGetDeviceProcAddr(c.dev, #name)
    DF(CreateBuffer, vkCreateBuffer); DF(DestroyBuffer, vkDestroyBuffer); DF(GetBufMemReq, vkGetBufferMemoryRequirements);
    DF(AllocateMemory, vkAllocateMemory); DF(FreeMemory, vkFreeMemory); DF(BindBufferMemory, vkBindBufferMemory);
    DF(MapMemory, vkMapMemory); DF(UnmapMemory, vkUnmapMemory); DF(AllocCB, vkAllocateCommandBuffers);
    DF(FreeCB, vkFreeCommandBuffers); DF(BeginCB, vkBeginCommandBuffer); DF(EndCB, vkEndCommandBuffer);
    DF(CmdFill, vkCmdFillBuffer); DF(CmdCopy, vkCmdCopyBuffer); DF(CmdBarrier, vkCmdPipelineBarrier);
    DF(CmdBindPipe, vkCmdBindPipeline); DF(CmdBindDS, vkCmdBindDescriptorSets); DF(CmdPush, vkCmdPushConstants);
    DF(CmdDispatch, vkCmdDispatch); DF(QueueSubmit, vkQueueSubmit); DF(CreateFence, vkCreateFence);
    DF(DestroyFence, vkDestroyFence); DF(WaitFences, vkWaitForFences); DF(CreateSM, vkCreateShaderModule);
    DF(DestroySM, vkDestroyShaderModule); DF(CreateDSL, vkCreateDescriptorSetLayout); DF(DestroyDSL, vkDestroyDescriptorSetLayout);
    DF(CreatePL, vkCreatePipelineLayout); DF(DestroyPL, vkDestroyPipelineLayout); DF(CreateCP, vkCreateComputePipelines);
    DF(DestroyPipe, vkDestroyPipeline); DF(CreateDP, vkCreateDescriptorPool); DF(DestroyDP, vkDestroyDescriptorPool);
    DF(AllocDS, vkAllocateDescriptorSets); DF(UpdateDS, vkUpdateDescriptorSets); DF(DeviceWaitIdle, vkDeviceWaitIdle);
    PFN_vkGetDeviceQueue gdq = (PFN_vkGetDeviceQueue)vkGetDeviceProcAddr(c.dev, "vkGetDeviceQueue");
    PFN_vkCreateCommandPool ccp = (PFN_vkCreateCommandPool)vkGetDeviceProcAddr(c.dev, "vkCreateCommandPool");
    gdq(c.dev, c.qfam, 0, &c.queue);
    VkCommandPoolCreateInfo pci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = c.qfam,
                                   .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT};
    ccp(c.dev, &pci, NULL, &c.pool);

    j_open("tests", '[');
    static const VkDeviceSize fills[] = {4092, 4096, 4100, 65536, 1u << 20, 16u << 20};
    for (unsigned i = 0; i < sizeof fills / sizeof *fills && !c.fatal; i++)
        if (level >= 2 || fills[i] <= (1u << 20)) t_fill(&c, fills[i]);
    if (!c.fatal) t_copy(&c, 1u << 20);
    if (!c.fatal && level >= 2) t_copy(&c, 64u << 20);
    if (!c.fatal) t_index(&c, 1, 1);
    if (!c.fatal) t_index(&c, 1024, 64);
    if (!c.fatal && level >= 2) t_index(&c, 65535, 1);
    if (!c.fatal) t_saxpy(&c, 1u << 20, 1);
    if (!c.fatal && level >= 2) t_saxpy(&c, 16u << 20, 1);
    if (!c.fatal) t_reduce(&c, 1u << 20);
    if (!c.fatal && level >= 2) t_saxpy(&c, 1u << 18, 200);
    j_close(']');
    j_kint("passed_count", n_pass);
    j_kint("failed_count", n_fail);
    j_kbool("fatal", c.fatal);
    if (c.fatal) j_kstr("fatal_reason", c.fatal_why);
    j_close('}');
    if (!c.fatal) {
        c.DeviceWaitIdle(c.dev);
        PFN_vkDestroyCommandPool dcp = (PFN_vkDestroyCommandPool)vkGetDeviceProcAddr(c.dev, "vkDestroyCommandPool");
        PFN_vkDestroyDevice dd = (PFN_vkDestroyDevice)vkGetDeviceProcAddr(c.dev, "vkDestroyDevice");
        dcp(c.dev, c.pool, NULL);
        dd(c.dev, NULL);
    }
    if (c.fatal) return 3;
    return n_fail ? 1 : 0;
}

static void usage(void) {
    fprintf(stderr,
        "orbis-gpu-probe " PROBE_VERSION "\n"
        "usage: orbis-gpu-probe [--json] [--gl] [--vk] [--compute] [--all]\n"
        "                       [--device N] [--queue-family N] [--timeout-ms MS] [--level 1|2]\n"
        "exit: 0 ok, 1 test failure, 2 setup error, 3 GPU timeout/device lost\n");
}

int main(int argc, char **argv) {
    bool gl = false, vk = false, comp = false;
    int dev = -1, qfam = -1, level = 1;
    uint64_t timeout_ms = 5000;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--json")) ;
        else if (!strcmp(argv[i], "--gl")) gl = true;
        else if (!strcmp(argv[i], "--vk")) vk = true;
        else if (!strcmp(argv[i], "--compute")) comp = vk = true;
        else if (!strcmp(argv[i], "--all")) gl = vk = comp = true;
        else if (!strcmp(argv[i], "--device") && i + 1 < argc) dev = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--queue-family") && i + 1 < argc) qfam = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--timeout-ms") && i + 1 < argc) timeout_ms = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--level") && i + 1 < argc) level = atoi(argv[++i]);
        else { usage(); return 2; }
    }
    if (!gl && !vk) gl = vk = true;
    J = stdout;
    setvbuf(stdout, NULL, _IOLBF, 0);
    int rc = 0;
    j_open(NULL, '{');
    j_kstr("probe_version", PROBE_VERSION);
    if (gl) { if (probe_gl()) rc = rc ? rc : 1; }
    if (vk) {
        int ir = vk_init();
        j_open("vulkan", '{');
        if (ir == -1) { j_kstr("error", "libvulkan.so.1 not found"); j_close('}'); rc = 2; goto out; }
        if (ir) { j_kstr("error", ir == -2 ? "no vkGetInstanceProcAddr" : vkres((VkResult)ir)); j_close('}'); rc = 2; goto out; }
        IF(vkEnumeratePhysicalDevices);
        uint32_t n = 0;
        vkEnumeratePhysicalDevices(inst, &n, NULL);
        VkPhysicalDevice pds[16];
        if (n > 16) n = 16;
        vkEnumeratePhysicalDevices(inst, &n, pds);
        j_open("devices", '[');
        for (uint32_t i = 0; i < n; i++) describe_device(pds[i], i);
        j_close(']');
        j_close('}');
        if (comp) {
            int pick = dev;
            if (pick < 0) {
                IF(vkGetPhysicalDeviceProperties);
                for (uint32_t i = 0; i < n && pick < 0; i++) {
                    VkPhysicalDeviceProperties p; vkGetPhysicalDeviceProperties(pds[i], &p);
                    if (p.deviceType != VK_PHYSICAL_DEVICE_TYPE_CPU) pick = (int)i;
                }
                if (pick < 0 && n) pick = 0;   /* software-only system: still test, caller flags it */
            }
            if (pick < 0 || (uint32_t)pick >= n) { j_kstr("compute_error", "no Vulkan device"); rc = 2; }
            else {
                int cr = run_compute(pds[pick], (uint32_t)pick, qfam, timeout_ms, level);
                if (cr) rc = cr;
            }
        }
    }
out:
    j_kint("exit_code", rc);
    j_close('}');
    fputc('\n', stdout);
    fflush(stdout);
    /* After a GPU timeout, skip vkDestroyInstance: the driver may block. */
    _exit(rc);
}
