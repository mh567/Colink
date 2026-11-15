#include <webkit2/webkit2.h>

GInputStream *webkit_uri_scheme_request_get_http_body(WebKitURISchemeRequest *request) {
    (void)request;
    return NULL;
}

void webkit_web_view_evaluate_javascript(WebKitWebView *web_view,
                                         const gchar *script,
                                         gssize length,
                                         const gchar *world_name,
                                         const gchar *source_uri,
                                         GCancellable *cancellable,
                                         GAsyncReadyCallback callback,
                                         gpointer user_data) {
    (void)length;
    (void)world_name;
    (void)source_uri;
    webkit_web_view_run_javascript(web_view, script, cancellable, callback, user_data);
}
