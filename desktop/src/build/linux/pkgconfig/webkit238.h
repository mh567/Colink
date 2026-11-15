#ifndef OCTOP_WEBKIT238_H
#define OCTOP_WEBKIT238_H

#include <webkit2/webkit2.h>

G_BEGIN_DECLS

GInputStream *webkit_uri_scheme_request_get_http_body(WebKitURISchemeRequest *request);
void webkit_web_view_evaluate_javascript(WebKitWebView *web_view,
                                         const gchar *script,
                                         gssize length,
                                         const gchar *world_name,
                                         const gchar *source_uri,
                                         GCancellable *cancellable,
                                         GAsyncReadyCallback callback,
                                         gpointer user_data);

G_END_DECLS

#endif
