# 빌드 컨텍스트: 프로젝트 루트
FROM nginx:1.27-alpine

COPY edge/templates /etc/nginx/templates
COPY web /usr/share/nginx/html

# 인증서는 docker-compose 에서 ./edge/certs 를 /etc/nginx/certs 로 마운트
EXPOSE 80 443
