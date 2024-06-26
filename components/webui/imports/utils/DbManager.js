import {logger} from "/imports/utils/logger";
import mysql from "mysql2/promise";

import {deinitStatsDbManager, initStatsDbManager} from "../api/ingestion/server/publications";
import {initSearchJobsDbManager} from "../api/search/server/methods";


const DB_CONNECTION_LIMIT = 2;
const DB_MAX_IDLE = DB_CONNECTION_LIMIT;
const DB_IDLE_TIMEOUT_IN_MS = 10000;

/**
 * @type {import("mysql2/promise").Pool|null}
 */
let dbConnPool = null;

/**
 * Creates a new database connection and initializes DB managers with it.
 *
 * @param {object} dbConfig
 * @param {string} dbConfig.dbHost
 * @param {number} dbConfig.dbPort
 * @param {string} dbConfig.dbName
 * @param {string} dbConfig.dbUser
 * @param {string} dbConfig.dbPassword
 *
 * @param {object} tableNames
 * @param {string} tableNames.searchJobsTableName
 * @param {string} tableNames.clpArchivesTableName
 * @param {string} tableNames.clpFilesTableName
 *
 * @returns {Promise<void>}
 * @throws {Error} on error.
 */
const initDbManagers = async ({
    dbHost,
    dbPort,
    dbName,
    dbUser,
    dbPassword,
}, {
    searchJobsTableName,
    clpArchivesTableName,
    clpFilesTableName,
}) => {
    if (null !== dbConnPool) {
        throw Error("This method should not be called twice.");
    }

    try {
        dbConnPool = await mysql.createPool({
            host: dbHost,
            port: dbPort,
            database: dbName,
            user: dbUser,
            password: dbPassword,
            bigNumberStrings: true,
            supportBigNumbers: true,
            enableKeepAlive: true,
            connectionLimit: DB_CONNECTION_LIMIT,
            maxIdle: DB_MAX_IDLE,
            idleTimeout: DB_IDLE_TIMEOUT_IN_MS
        });

        initSearchJobsDbManager(dbConnPool, {
            searchJobsTableName,
        });
        initStatsDbManager(dbConnPool, {
            clpArchivesTableName,
            clpFilesTableName,
        });
    } catch (e) {
        logger.error("Unable to create MySQL / mariadb connection pool.", e.toString());
        throw e;
    }
};

/**
 * De-initialize database managers.
 * @returns {Promise<void>}
 * @throws {Error} on error.
 */
const deinitDbManagers = async () => {
    deinitStatsDbManager();

    await dbConnPool.end();
};

export {initDbManagers, deinitDbManagers};
