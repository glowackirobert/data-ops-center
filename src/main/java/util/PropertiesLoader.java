package util;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

public class PropertiesLoader {
    private static final Logger LOG = LoggerFactory.getLogger(PropertiesLoader.class);

    public static Properties loadProperties(String propertiesFile) {
        LOG.info("Loading properties from: {}", propertiesFile);

        try (InputStream inputStream = PropertiesLoader.class.getClassLoader().getResourceAsStream(propertiesFile)) {
            if (inputStream == null) {
                LOG.error("Properties file not found: {}", propertiesFile);
                return null;
            }

            Properties properties = new Properties();
            properties.load(inputStream);
            LOG.debug("Loaded {} properties", properties.size());
            return properties;

        } catch (IOException e) {
            LOG.error("Failed to load properties from {}", propertiesFile, e);
            return null;
        }
    }
}