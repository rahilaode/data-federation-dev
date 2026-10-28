# Teiid 16.0.0 di atas WildFly 19.1.0, sama dengan setup/data-federation/Dockerfile,
# tetapi dengan datasource contoh multi-source (setup.cli di folder ini).
FROM jboss/wildfly:19.1.0.Final

ENV JBOSS_HOME /opt/jboss/wildfly
ENV TEIID_VERSION 16.0.0

# Distribusi Teiid dari Maven Central, diperiksa dengan checksum .sha1
ARG MAVEN_REPO=https://repo1.maven.org/maven2
RUN cd $JBOSS_HOME \
    && TEIID_ZIP=teiid-wildfly-$TEIID_VERSION-dist.zip \
    && TEIID_URL=$MAVEN_REPO/org/teiid/wildfly/teiid-wildfly/$TEIID_VERSION/$TEIID_ZIP \
    && curl -fSL --retry 3 -o $TEIID_ZIP $TEIID_URL \
    && echo "$(curl -fsSL $TEIID_URL.sha1 | cut -d' ' -f1)  $TEIID_ZIP" | sha1sum -c - \
    && bsdtar -xf $TEIID_ZIP \
    && chmod +x $JBOSS_HOME/bin/*.sh \
    && rm $TEIID_ZIP

# ManagementRealm (konsol 9990) dan ApplicationRealm (JDBC/ODBC)
ARG ADMIN_USER=admin
ARG ADMIN_PASSWORD=Password12345_
RUN $JBOSS_HOME/bin/add-user.sh ${ADMIN_USER} ${ADMIN_PASSWORD} --silent --enable

ARG APP_USER=user1
ARG APP_PASSWORD=Password12345_
ARG APP_GROUP="odata,rest"
RUN $JBOSS_HOME/bin/add-user.sh -a ${APP_USER} -p ${APP_PASSWORD} -r ApplicationRealm -g ${APP_GROUP} --silent --enable

ENV PATH=$PATH:${JBOSS_HOME}/bin
WORKDIR ${JBOSS_HOME}

COPY jdbc /opt/jboss/jdbc
COPY setup.cli /opt/jboss/example/setup.cli

# Driver JDBC dan tiga datasource dipasang sekali saat build
RUN /bin/sh -c "${JBOSS_HOME}/bin/standalone.sh -c standalone-teiid.xml -b 0.0.0.0 -bmanagement 0.0.0.0 &" && \
      sleep 20 && \
      cd /tmp && \
      $JBOSS_HOME/bin/jboss-cli.sh --file=/opt/jboss/example/setup.cli && \
      $JBOSS_HOME/bin/jboss-cli.sh --connect --command=:shutdown

CMD ["/bin/sh", "-c", "${JBOSS_HOME}/bin/standalone.sh -c standalone-teiid.xml -b 0.0.0.0 -bmanagement 0.0.0.0"]
