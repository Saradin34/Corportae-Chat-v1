FROM nginx:1.27-alpine

# Bust Docker cache so frontend/html/ is always re-copied on rebuild
ARG CACHEBUST=1
RUN echo "$CACHEBUST"

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/nginx.conf /etc/nginx/nginx.conf

# Static frontend
COPY frontend/html/ /usr/share/nginx/html/

EXPOSE 80
